"""Phase 23 M2 — no topic-lock capability hints."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import format_capability_hints
from session import create_new
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths

_LOCK_PHRASES = (
    "确认 workflow 主题后可用",
    "确认 coding 主题后可用",
    "未确认主题时仅有 common",
)


class ToolCatalogM2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=(
                "common/write_text",
                "coding/patch_file",
                "workflow/sort_by_extension",
            ),
        )
        self.registry = ToolRegistry.load(self.paths)

    def test_no_confirm_topic_hints_when_topics_empty(self) -> None:
        session = create_new(
            self.paths,
            conversation_id=f"_m2_empty_{secrets.token_hex(3)}",
        )
        session.meta.topics = []
        session.save()
        text = format_capability_hints(session, registry=self.registry)
        for phrase in _LOCK_PHRASES:
            self.assertNotIn(phrase, text)
        self.assertIn("执行面", text)

    def test_no_confirm_topic_hints_when_coding_only(self) -> None:
        session = create_new(
            self.paths,
            conversation_id=f"_m2_coding_{secrets.token_hex(3)}",
        )
        session.meta.topics = ["coding"]
        session.save()
        text = format_capability_hints(session, registry=self.registry)
        for phrase in _LOCK_PHRASES:
            self.assertNotIn(phrase, text)
        self.assertNotIn("确认 workflow 主题", text)
        self.assertNotIn("确认 coding 主题后可用", text)


if __name__ == "__main__":
    unittest.main()
