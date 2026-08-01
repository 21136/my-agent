"""Phase 23 M3 — system overlay injects INDEX, not full evolved catalog."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import (
    build_system_prompt,
    format_evolved_catalog_overlay,
    load_tool_catalog_index,
)
from paths import AgentPaths
from session import create_new
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths


class ToolCatalogM3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_text", "coding/patch_file"),
        )
        # Copy INDEX into isolated agent root
        src = _ROOT / "evolve" / "tool-catalog"
        dest = self.paths.agent_root / "evolve" / "tool-catalog"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "INDEX.md").write_text(
            (src / "INDEX.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.registry = ToolRegistry.load(self.paths)

    def test_overlay_contains_index_not_full_tool_list(self) -> None:
        session = create_new(
            self.paths,
            conversation_id=f"_m3_{secrets.token_hex(3)}",
        )
        session.meta.topics = ["coding"]
        session.save()
        overlay = format_evolved_catalog_overlay(session, registry=self.registry)
        self.assertIn("工具索引", overlay)
        self.assertIn("buckets/write.md", overlay)
        # Full catalog style lines like "- patch_file: ..." should not dominate
        self.assertNotIn("- patch_file:", overlay)
        self.assertNotIn("## coding（本会话主题）", overlay)

    def test_system_prompt_includes_index(self) -> None:
        session = create_new(
            self.paths,
            conversation_id=f"_m3_sys_{secrets.token_hex(3)}",
        )
        session.meta.topics = []
        session.save()
        built = build_system_prompt(
            session,
            paths=self.paths,
            agent_core_dir=_AGENT_CORE,
            registry=self.registry,
        )
        self.assertIn("工具索引", built.prompt)
        self.assertIn("evolve/tool-catalog", built.prompt)

    def test_index_truncation(self) -> None:
        paths = self.paths
        index_path = paths.agent_root / "evolve" / "tool-catalog" / "INDEX.md"
        index_path.write_text("X" * 3000, encoding="utf-8")
        text = load_tool_catalog_index(paths, max_chars=500)
        self.assertLessEqual(len(text), 500 + 20)
        self.assertIn("截断", text)


if __name__ == "__main__":
    unittest.main()
