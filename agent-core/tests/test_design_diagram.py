"""Focused tests for the design_diagram evolved tool."""

from __future__ import annotations

import importlib.util
import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))


def _load_tool():
    tool_path = _ROOT / "evolve" / "tools" / "workflow" / "design_diagram" / "main.py"
    spec = importlib.util.spec_from_file_location("design_diagram_under_test", tool_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesignDiagramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_auto_selects_mermaid_for_sequence(self) -> None:
        result = self.tool.run_design_diagram(
            {
                "path": "workspace/_design_diagram_test.mmd",
                "diagram_type": "sequence",
                "source": "sequenceDiagram\n    Client->>Service: Request",
                "dry_run": True,
            }
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "mermaid")
        self.assertTrue(result["dry_run"])

    def test_auto_selects_plantuml_for_use_case(self) -> None:
        result = self.tool.run_design_diagram(
            {
                "path": "workspace/_design_diagram_test.puml",
                "diagram_type": "use_case",
                "source": "actor User\nUser --> (Login)",
                "dry_run": True,
            }
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "plantuml")

    def test_rejects_engine_and_suffix_mismatch(self) -> None:
        result = self.tool.run_design_diagram(
            {
                "path": "workspace/_design_diagram_test.puml",
                "diagram_type": "sequence",
                "source": "sequenceDiagram\n    A->>B: C",
            }
        )
        self.assertFalse(result["ok"])
        self.assertIn("Mermaid output requires", result["error"])

    def test_writes_wrapped_plantuml_source(self) -> None:
        relative_path = f"workspace/_design_diagram_{secrets.token_hex(4)}.puml"
        target = _ROOT / relative_path
        try:
            result = self.tool.run_design_diagram(
                {
                    "path": relative_path,
                    "diagram_type": "class",
                    "source": "class User",
                    "on_conflict": "overwrite",
                }
            )
            self.assertTrue(result["ok"])
            self.assertTrue(target.is_file())
            source = target.read_text(encoding="utf-8")
            self.assertIn("@startuml", source)
            self.assertIn("@enduml", source)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
