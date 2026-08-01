"""Phase 23 Mq — product map: desktop two windows, no grow/daily as user-facing shells."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

_FORBIDDEN_USER_FACING = (
    "须用户切 **grow** 壳",
    "切 **grow**",
    "target=grow|daily|project",
    "顶栏切壳",
    "one CLI session",
)


class ToolCatalogMqTests(unittest.TestCase):
    def test_product_map_phrases(self) -> None:
        core = (_AGENT_CORE / "prompts" / "core.txt").read_text(encoding="utf-8")
        project = (_ROOT / "evolve" / "prompts" / "project.md").read_text(encoding="utf-8")
        coding = (_ROOT / "evolve" / "prompts" / "coding.md").read_text(encoding="utf-8")
        blob = "\n".join([core, project, coding])
        for phrase in _FORBIDDEN_USER_FACING:
            self.assertNotIn(phrase, blob, msg=f"过时产品措辞: {phrase!r}")
        self.assertIn("普通窗口", core)
        self.assertIn("项目窗口", core)
        self.assertIn("普通窗口", project)
        self.assertIn("用户项目", coding)
        self.assertIn("维护 my-agent 内核", coding)


if __name__ == "__main__":
    unittest.main()
