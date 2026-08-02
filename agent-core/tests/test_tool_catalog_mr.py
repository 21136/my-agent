"""Phase 23 Mr — write_evolve handbook lives in evolve bucket; core stays lean."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

_BUCKET = "evolve/tool-catalog/buckets/evolve.md"
_CORE_FORBIDDEN = ("_staging", "on_conflict", "content_workspace_path", "content_base64")


class ToolCatalogMrTests(unittest.TestCase):
    def test_core_points_to_bucket_without_handbook(self) -> None:
        core = (_AGENT_CORE / "prompts" / "core.txt").read_text(encoding="utf-8")
        self.assertIn(_BUCKET, core)
        for term in _CORE_FORBIDDEN:
            self.assertNotIn(term, core, msg=f"core 不应再含细则词: {term!r}")
        self.assertNotIn("8. Each write requires", core)
        self.assertLess(len(core.splitlines()), 85)

    def test_bucket_has_handbook(self) -> None:
        bucket = (_ROOT / "evolve" / "tool-catalog" / "buckets" / "evolve.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("write_evolve", bucket)
        self.assertIn("on_conflict", bucket)
        self.assertIn("content_workspace_path", bucket)
        self.assertIn("_staging", bucket)

    def test_topic_prompts_point_not_duplicate(self) -> None:
        coding = (_ROOT / "evolve" / "prompts" / "coding.md").read_text(encoding="utf-8")
        data = (_ROOT / "evolve" / "prompts" / "data.md").read_text(encoding="utf-8")
        for blob, name in ((coding, "coding"), (data, "data")):
            self.assertIn("buckets/evolve.md", blob, msg=name)
            self.assertNotIn("content_base64", blob, msg=name)
            self.assertNotIn("content_workspace_path", blob, msg=name)


if __name__ == "__main__":
    unittest.main()
