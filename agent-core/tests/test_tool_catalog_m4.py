"""Phase 23 M4 — tool-catalog buckets list active tools."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

_BUCKETS = _ROOT / "evolve" / "tool-catalog" / "buckets"

# Minimal active names that must appear in some bucket (M4 registry of record).
_REQUIRED = {
    "write.md": ("write_text", "append_text", "copy_move", "move_to_trash", "patch_file"),
    "run.md": ("run_python", "npm_exec", "mvn_exec", "run_demo", "csv_head", "run_service"),
    "organize.md": ("sort_by_extension", "rename_batch", "dedupe_by_name"),
    "project.md": ("report_progress", "project_catalog"),
    "evolve.md": ("write_evolve", "git_clone"),
}


class ToolCatalogM4Tests(unittest.TestCase):
    def test_buckets_list_required_tools(self) -> None:
        for name, tools in _REQUIRED.items():
            path = _BUCKETS / name
            self.assertTrue(path.is_file(), msg=str(path))
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("正文待 M4", text)
            for tool in tools:
                self.assertIn(f"`{tool}`", text, msg=f"{name} missing {tool}")

    def test_index_under_2kb(self) -> None:
        index = _ROOT / "evolve" / "tool-catalog" / "INDEX.md"
        self.assertLessEqual(index.stat().st_size, 2048)


if __name__ == "__main__":
    unittest.main()
