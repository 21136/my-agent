"""Focused tests for the render_diagram evolved tool."""

from __future__ import annotations

import importlib.util
import secrets
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))


def _load_tool():
    tool_path = _ROOT / "evolve" / "tools" / "workflow" / "render_diagram" / "main.py"
    spec = importlib.util.spec_from_file_location("render_diagram_under_test", tool_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RenderDiagramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_rejects_unknown_output_format(self) -> None:
        source_path = _ROOT / "workspace" / f"_render_diagram_{secrets.token_hex(4)}.mmd"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("sequenceDiagram\n    A->>B: C\n", encoding="utf-8")
        try:
            result = self.tool.run_render_diagram(
                {"source_path": str(source_path), "output_path": "workspace/result.bmp", "engine": "mermaid"}
            )
            self.assertFalse(result["ok"])
            self.assertIn(".png", result["error"])
        finally:
            source_path.unlink(missing_ok=True)

    def test_dry_run_builds_mermaid_command(self) -> None:
        source_path = _ROOT / "workspace" / f"_render_diagram_{secrets.token_hex(4)}.mmd"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("sequenceDiagram\n    A->>B: C\n", encoding="utf-8")
        try:
            with patch.object(self.tool, "_renderer_command", return_value=("fake", ["mmdc"])):
                result = self.tool.run_render_diagram(
                    {
                        "source_path": str(source_path),
                        "output_path": "workspace/result.png",
                        "engine": "mermaid",
                        "dry_run": True,
                    }
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["renderer"], "fake")
            self.assertIn("-i", result["would_run"])
            self.assertIn("-o", result["would_run"])
        finally:
            source_path.unlink(missing_ok=True)

    def test_runs_renderer_and_reports_output(self) -> None:
        source_path = _ROOT / "workspace" / f"_render_diagram_{secrets.token_hex(4)}.mmd"
        output_path = _ROOT / "workspace" / f"_render_diagram_{secrets.token_hex(4)}.png"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("sequenceDiagram\n    A->>B: C\n", encoding="utf-8")
        script = "from pathlib import Path; import sys; Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'PNG')"
        try:
            with patch.object(self.tool, "_renderer_command", return_value=("fake", [sys.executable, "-c", script])):
                result = self.tool.run_render_diagram(
                    {
                        "source_path": str(source_path),
                        "output_path": str(output_path),
                        "engine": "mermaid",
                        "on_conflict": "overwrite",
                    }
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["written"])
            self.assertEqual(output_path.read_bytes(), b"PNG")
        finally:
            source_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
