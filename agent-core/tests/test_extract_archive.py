"""Tests for workflow extract_archive evolved tool."""

from __future__ import annotations

import shutil
import unittest
import zipfile
from pathlib import Path

from tests.isolation_helpers import temporary_agent_paths
from tools.builtin.run_evolved import run
from tools.registry import ToolRegistry


class ExtractArchiveTests(unittest.TestCase):
    def test_python_backend_extract_and_dry_run(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("workflow/extract_archive",)) as paths:
            registry = ToolRegistry.load(paths)
            demo_dir = paths.workspace / "_extract_it"
            demo_dir.mkdir(parents=True, exist_ok=True)
            archive = demo_dir / "sample.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("hello.txt", "world")

            rel_archive = paths.to_agent_relative(archive)

            dry = run(
                {
                    "tool_name": "extract_archive",
                    "arguments": {
                        "archive_path": rel_archive,
                        "backend": "python",
                    },
                    "dry_run": True,
                },
                registry=registry,
            )
            self.assertTrue(dry.ok, dry.error)
            self.assertEqual(dry.data.get("backend"), "python")
            self.assertGreaterEqual(dry.data.get("entry_count", 0), 1)
            self.assertIn("hello.txt", dry.data.get("listing", ""))

            live = run(
                {
                    "tool_name": "extract_archive",
                    "arguments": {
                        "archive_path": rel_archive,
                        "backend": "python",
                    },
                    "dry_run": False,
                },
                registry=registry,
            )
            self.assertTrue(live.ok, live.error)
            extracted = demo_dir / "sample" / "hello.txt"
            self.assertTrue(extracted.is_file())
            self.assertEqual(extracted.read_text(encoding="utf-8"), "world")

            shutil.rmtree(demo_dir, ignore_errors=True)

    def test_missing_archive_rejected(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("workflow/extract_archive",)) as paths:
            registry = ToolRegistry.load(paths)
            result = run(
                {
                    "tool_name": "extract_archive",
                    "arguments": {
                        "archive_path": "workspace/_missing_archive.zip",
                        "backend": "python",
                    },
                    "dry_run": True,
                },
                registry=registry,
            )
            self.assertFalse(result.ok)

    def test_registry_loads_tool(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("workflow/extract_archive",)) as paths:
            registry = ToolRegistry.load(paths)
            tool = registry.get_evolved("extract_archive")
            self.assertIsNotNone(tool)
            assert tool is not None
            self.assertEqual(tool.scope, "workflow")
            self.assertEqual(tool.status, "active")


if __name__ == "__main__":
    unittest.main()
