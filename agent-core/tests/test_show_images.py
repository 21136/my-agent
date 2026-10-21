"""show_images builtin and display_paths on tool.end."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import build_builtin_tool_definition
from tools.builtin.show_images import run
from tools.executor import ExecutorSession, ToolExecutor, _tool_end_display_paths
from tools.registry import ToolRegistry
from tests.isolation_helpers import make_temp_agent_paths


PNG_BYTES = b"\x89PNG\r\n\x1a\n"


class ShowImagesBuiltinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.image_rel = "workspace/demo/x.png"
        target = self.paths.resolve_under_agent(self.image_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PNG_BYTES)

    def test_shows_valid_image_paths(self) -> None:
        result = run({"paths": [self.image_rel]}, paths=self.paths)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["display_paths"], [self.image_rel])
        self.assertEqual(result.data["count"], 1)

    def test_rejects_missing_paths(self) -> None:
        result = run({"paths": ["workspace/nope/missing.png"]}, paths=self.paths)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "no_images")

    def test_schema_registered(self) -> None:
        definition = build_builtin_tool_definition("show_images")
        self.assertEqual(definition["function"]["name"], "show_images")
        props = definition["function"]["parameters"]["properties"]
        self.assertIn("paths", props)


class DisplayPathsHelperTests(unittest.TestCase):
    def test_normalizes_display_paths(self) -> None:
        out = _tool_end_display_paths(
            {"display_paths": [" workspace/a.png ", "workspace\\b.jpg"]}
        )
        self.assertEqual(out, ["workspace/a.png", "workspace/b.jpg"])


class ShowImagesExecutorEventTests(unittest.TestCase):
    def test_tool_end_includes_display_paths(self) -> None:
        paths = make_temp_agent_paths(self)
        rel = "workspace/show-me.png"
        img = paths.resolve_under_agent(rel)
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(PNG_BYTES)

        registry = ToolRegistry.load(paths)
        events: list[tuple[str, dict]] = []

        def on_event(kind: str, payload: dict) -> None:
            events.append((kind, payload))

        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(),
            on_event=on_event,
        )
        result = executor.run("show_images", {"paths": [rel]})
        self.assertTrue(result.ok)
        self.assertTrue(result.data.get("desktop_chat_inline_display"))
        self.assertIn("llm_hint", result.data)
        self.assertIn("Do not ask", result.data["llm_hint"])
        end = next(p for k, p in events if k == "tool.end")
        self.assertEqual(end["display_paths"], [rel])
        self.assertIn("内联展示", end["summary"])


if __name__ == "__main__":
    unittest.main()
