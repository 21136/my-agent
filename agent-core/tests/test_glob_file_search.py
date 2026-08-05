"""Tests for glob_file_search builtin (Phase 42 · IT-430/431/432)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tools.builtin import glob_file_search
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode
from tests.isolation_helpers import temporary_agent_paths


class GlobFileSearchTests(unittest.TestCase):
    def test_it430_basic_match_and_truncation(self) -> None:
        with temporary_agent_paths() as paths:
            demo = paths.workspace / "glob-demo"
            demo.mkdir(parents=True, exist_ok=True)
            (demo / "one.py").write_text("1\n", encoding="utf-8")
            (demo / "two.py").write_text("2\n", encoding="utf-8")
            sub = demo / "pkg"
            sub.mkdir()
            (sub / "test_a.py").write_text("a\n", encoding="utf-8")

            result = glob_file_search.run(
                {"pattern": "**/*.py", "path": "workspace/glob-demo"},
                paths=paths,
            )
            self.assertTrue(result.ok, result.error.message if result.error else "")
            rel_paths = result.data.get("paths", [])
            self.assertIn("one.py", rel_paths)
            self.assertIn("pkg/test_a.py", rel_paths)
            self.assertFalse(result.truncated)

            capped = glob_file_search.run(
                {
                    "pattern": "**/*.py",
                    "path": "workspace/glob-demo",
                    "max_results": 1,
                },
                paths=paths,
            )
            self.assertTrue(capped.ok)
            self.assertEqual(len(capped.data.get("paths", [])), 1)
            self.assertTrue(capped.data.get("truncated"))

    def test_it430_default_path_is_agent_root(self) -> None:
        with temporary_agent_paths() as paths:
            marker = paths.workspace / "marker.globtest"
            marker.write_text("x\n", encoding="utf-8")
            result = glob_file_search.run(
                {"pattern": "**/marker.globtest"},
                paths=paths,
            )
            self.assertTrue(result.ok)
            self.assertIn("workspace/marker.globtest", result.data.get("paths", []))

    def test_it431_out_of_bounds_rejected(self) -> None:
        with temporary_agent_paths() as paths:
            result = glob_file_search.run(
                {"pattern": "*.py", "path": "../../../outside"},
                paths=paths,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, ToolErrorCode.PATH_OUT_OF_BOUNDS)

    def test_max_results_hard_cap(self) -> None:
        with temporary_agent_paths() as paths:
            demo = paths.workspace / "cap-demo"
            demo.mkdir()
            for i in range(5):
                (demo / f"f{i}.txt").write_text("x\n", encoding="utf-8")
            result = glob_file_search.run(
                {
                    "pattern": "*.txt",
                    "path": "workspace/cap-demo",
                    "max_results": 9999,
                },
                paths=paths,
            )
            self.assertTrue(result.ok)
            self.assertLessEqual(len(result.data.get("paths", [])), glob_file_search.HARD_MAX_RESULTS)

    def test_it432_gitignore_skips_node_modules(self) -> None:
        with temporary_agent_paths() as paths:
            demo = paths.workspace / "ignore-demo"
            demo.mkdir(parents=True, exist_ok=True)
            (demo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            src = demo / "src"
            src.mkdir()
            (src / "app.py").write_text("ok\n", encoding="utf-8")
            ignored = demo / "node_modules"
            ignored.mkdir()
            (ignored / "hidden.py").write_text("no\n", encoding="utf-8")

            with mock.patch("tools.builtin.glob_file_search.shutil.which", return_value=None):
                result = glob_file_search.run(
                    {"pattern": "**/*.py", "path": "workspace/ignore-demo"},
                    paths=paths,
                )
            self.assertTrue(result.ok, result.error.message if result.error else "")
            rel_paths = result.data.get("paths", [])
            self.assertIn("src/app.py", rel_paths)
            self.assertFalse(any("node_modules" in p for p in rel_paths))


class GlobFileSearchWiringTests(unittest.TestCase):
    def test_build_llm_tools_includes_glob(self) -> None:
        from agent import build_llm_tools
        from session import create_new
        import secrets

        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id=f"_globw-{secrets.token_hex(3)}")
            session.meta.turn_mode = "agent"
            names = {t["function"]["name"] for t in build_llm_tools(session)}
            self.assertIn("glob_file_search", names)

    def test_executor_runs_glob_builtin(self) -> None:
        with temporary_agent_paths() as paths:
            demo = paths.workspace / "wire-demo"
            demo.mkdir(parents=True, exist_ok=True)
            (demo / "hit.py").write_text("# x\n", encoding="utf-8")
            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(registry=registry)
            result = executor.run(
                "glob_file_search",
                {"pattern": "*.py", "path": "workspace/wire-demo"},
            )
            self.assertTrue(result.ok, result.error.message if result.error else "")
            self.assertIn("hit.py", result.data.get("paths", []))


class CorePromptGlobTests(unittest.TestCase):
    def test_core_txt_mentions_glob_file_search(self) -> None:
        core_path = _AGENT_CORE / "prompts" / "core.txt"
        text = core_path.read_text(encoding="utf-8")
        self.assertIn("glob_file_search", text)
        self.assertIn("Find files by name (glob)", text)


if __name__ == "__main__":
    unittest.main()
