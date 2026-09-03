"""Tests for runaway verify matrix linter (Phase 59 · IT-5961/5962)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from runaway_verify_matrix import lint_verify_matrix
from tests.isolation_helpers import temporary_agent_paths


class RunawayVerifyMatrixTests(unittest.TestCase):
    def _write_core(self, root: Path) -> None:
        (root / "PROJECT.md").write_text(
            "## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
            encoding="utf-8",
        )
        (root / "DESIGN.md").write_text("# Design\n", encoding="utf-8")
        (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
        (root / "VERIFY.md").write_text("- V-001 T-001 pass\n", encoding="utf-8")
        (root / "ENV.md").write_text(
            "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
            encoding="utf-8",
        )

    def test_mx5_missing_quality_commands(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "ENV.md").unlink()
            result = lint_verify_matrix(paths, pid)
            rules = {item.rule_id for item in result.errors}
            self.assertIn("MX-5", rules)

    def test_mx3_missing_acceptance_command(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text("# Demo\n", encoding="utf-8")
            (root / "DESIGN.md").write_text("# Design\n", encoding="utf-8")
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("- V-001 T-001 pass\n", encoding="utf-8")
            result = lint_verify_matrix(paths, pid)
            rules = {item.rule_id for item in result.errors}
            self.assertIn("MX-3", rules)

    def test_mx1_open_task_without_verify(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "TASKS.md").write_text("- [ ] T-002 open task\n", encoding="utf-8")
            result = lint_verify_matrix(paths, pid)
            rules = {item.rule_id for item in result.errors}
            self.assertIn("MX-1", rules)

    def test_matrix_passes_when_consistent(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            result = lint_verify_matrix(paths, pid)
            self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
