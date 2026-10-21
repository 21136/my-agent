"""The generated image builtin must be discoverable from the runtime tool guidance."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import format_builtin_summary, load_tool_catalog_index
from paths import AgentPaths


class GenerateImageToolCatalogTests(unittest.TestCase):
    def test_image_tool_is_named_in_runtime_index_and_builtin_summary(self) -> None:
        paths = AgentPaths.discover()
        index = load_tool_catalog_index(paths)
        builtin_summary = format_builtin_summary()

        self.assertIn("generate_image", index)
        self.assertIn("show_images", index)
        self.assertIn("generate_image", builtin_summary)
        self.assertIn("show_images", builtin_summary)
        self.assertIn("credits", builtin_summary.casefold())


    def test_image_tool_is_listed_in_operator_tool_documents(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        tools_doc = (repo_root / "docs" / "TOOLS.md").read_text(encoding="utf-8")
        map_doc = (repo_root / "docs" / "MAP.md").read_text(encoding="utf-8")

        self.assertIn("| `generate_image` |", tools_doc)
        self.assertIn("| `show_images` |", tools_doc)
        self.assertIn("| `generate_image` | `builtin/generate_image.py` |", map_doc)
        self.assertIn("| `show_images` | `builtin/show_images.py` |", map_doc)


if __name__ == "__main__":
    unittest.main()
