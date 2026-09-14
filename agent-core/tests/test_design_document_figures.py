"""Figure insertion tests for the design_document evolved tool."""

from __future__ import annotations

import base64
import importlib.util
import secrets
import sys
import unittest
import zipfile
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _load_tool():
    tool_path = _ROOT / "evolve" / "tools" / "workflow" / "design_document" / "main.py"
    spec = importlib.util.spec_from_file_location("design_document_figures_under_test", tool_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesignDocumentFiguresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_markdown_contains_figure_reference(self) -> None:
        image_path = _ROOT / "workspace" / f"_design_document_{secrets.token_hex(4)}.png"
        document_path = _ROOT / "workspace" / f"_design_document_{secrets.token_hex(4)}.md"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(_PNG_1X1)
        try:
            result = self.tool.run_design_document(
                {
                    "path": str(document_path),
                    "doc_type": "high_level_design",
                    "output_format": "markdown",
                    "figures": [
                        {
                            "path": str(image_path),
                            "section": "2. 总体架构",
                            "caption": "系统上下文图",
                            "alt_text": "客户端与服务关系",
                        }
                    ],
                }
            )
            self.assertTrue(result["ok"])
            markdown = document_path.read_text(encoding="utf-8")
            self.assertIn("![客户端与服务关系]", markdown)
            self.assertIn("系统上下文图", markdown)
        finally:
            image_path.unlink(missing_ok=True)
            document_path.unlink(missing_ok=True)

    def test_docx_embeds_figure_and_alt_text(self) -> None:
        image_path = _ROOT / "workspace" / f"_design_document_{secrets.token_hex(4)}.png"
        document_path = _ROOT / "workspace" / f"_design_document_{secrets.token_hex(4)}.docx"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(_PNG_1X1)
        try:
            result = self.tool.run_design_document(
                {
                    "path": str(document_path),
                    "doc_type": "high_level_design",
                    "output_format": "docx",
                    "figures": [
                        {
                            "path": str(image_path),
                            "section": "2. 总体架构",
                            "caption": "系统上下文图",
                            "alt_text": "客户端与服务关系",
                        }
                    ],
                }
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["figures"], 1)
            with zipfile.ZipFile(document_path) as archive:
                media_files = [name for name in archive.namelist() if name.startswith("word/media/")]
                document_xml = archive.read("word/document.xml").decode("utf-8")
            self.assertEqual(len(media_files), 1)
            self.assertIn("descr=\"客户端与服务关系\"", document_xml)
            self.assertIn("系统上下文图", document_xml)
        finally:
            image_path.unlink(missing_ok=True)
            document_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
