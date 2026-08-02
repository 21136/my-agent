"""IT-120 · Phase 30 Track C — append_text archived."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


class AppendTextArchivedTests(unittest.TestCase):
    def test_it120_append_text_not_callable(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/append_text", "common/write_text", "coding/patch_file")
        ) as paths:
            registry = ToolRegistry.load(paths)
            append = registry.get_evolved("append_text")
            self.assertIsNotNone(append)
            self.assertEqual(append.status, "archived")

            active = {t.name for t in registry.evolved() if t.status == "active"}
            self.assertNotIn("append_text", active)
            self.assertIn("write_text", active)
            self.assertIn("patch_file", active)

            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"append_text", "write_text"}),
                confirm_fn=confirm_fn,
            )
            result = executor.run(
                "run_evolved",
                {
                    "tool_name": "append_text",
                    "arguments": {"path": "workspace/x.txt", "content": "a"},
                },
            )
            self.assertFalse(result.ok)
            msg = (result.error.message if result.error else "") or ""
            self.assertIn("不可执行", msg)
            self.assertEqual(confirms, [])


if __name__ == "__main__":
    unittest.main()
