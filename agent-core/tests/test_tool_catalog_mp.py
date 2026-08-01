"""Phase 23 Mp — prompts must not teach topic execution locks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

_FORBIDDEN = (
    "must appear in the session evolved catalog",
    "确认 workflow 主题后",
    "确认 coding 主题后可用",
    "确认会话含 **data** 主题",
    "确认 **workflow** 主题后",
)


class ToolCatalogMpPromptTests(unittest.TestCase):
    def test_no_topic_execution_lock_phrases(self) -> None:
        paths = [
            _AGENT_CORE / "prompts" / "core.txt",
            _ROOT / "evolve" / "prompts" / "workflow.md",
            _ROOT / "evolve" / "prompts" / "coding.md",
            _ROOT / "evolve" / "prompts" / "data.md",
        ]
        blob = "\n".join(p.read_text(encoding="utf-8") for p in paths)
        for phrase in _FORBIDDEN:
            self.assertNotIn(phrase, blob, msg=f"残留硬锁措辞: {phrase!r}")


if __name__ == "__main__":
    unittest.main()
