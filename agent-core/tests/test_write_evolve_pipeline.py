"""Focused tests for write_evolve / run_evolved pipeline (P2)."""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import detect_scaffold_tool_turn, format_write_evolve_cookbook
from tools.builtin import run_evolved
from tools.executor import (
    ExecutorSession,
    ToolExecutor,
    _validate_scaffold_evolved_call,
    _write_evolve_wrote_tool_manifest,
)
from tools.schema import ToolResult, tool_ok


class CoalesceTests(unittest.TestCase):
    def test_merges_top_level_write_evolve_fields(self) -> None:
        merged = run_evolved.coalesce_tool_arguments(
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/x/main.py",
                "content_base64": "YQ==",
                "on_conflict": "overwrite",
            }
        )
        self.assertEqual(merged["path"], "evolve/tools/common/x/main.py")
        self.assertEqual(merged["content_base64"], "YQ==")
        self.assertEqual(merged["on_conflict"], "overwrite")

    def test_inner_arguments_override_top_level(self) -> None:
        merged = run_evolved.coalesce_tool_arguments(
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/top/main.py",
                "arguments": {"path": "evolve/tools/common/inner/main.py"},
            }
        )
        self.assertEqual(merged["path"], "evolve/tools/common/inner/main.py")

    def test_non_write_evolve_ignores_top_level_path(self) -> None:
        merged = run_evolved.coalesce_tool_arguments(
            {
                "tool_name": "write_text",
                "path": "should-not-merge",
                "arguments": {"path": "workspace/a.txt"},
            }
        )
        self.assertEqual(merged, {"path": "workspace/a.txt"})


class DryRunPrecedenceTests(unittest.TestCase):
    def test_outer_false_inner_true(self) -> None:
        outer = {"dry_run": False, "arguments": {"dry_run": True}}
        dry = bool(outer.get("dry_run", False))
        inner = outer.get("arguments")
        if not dry and isinstance(inner, dict) and isinstance(inner.get("dry_run"), bool):
            dry = inner["dry_run"]
        self.assertTrue(dry)

    def test_outer_true_wins(self) -> None:
        outer = {"dry_run": True, "arguments": {"dry_run": False}}
        dry = bool(outer.get("dry_run", False))
        inner = outer.get("arguments")
        if not dry and isinstance(inner, dict) and isinstance(inner.get("dry_run"), bool):
            dry = inner["dry_run"]
        self.assertTrue(dry)


class ScaffoldDetectTests(unittest.TestCase):
    def test_chinese_markers(self) -> None:
        self.assertTrue(detect_scaffold_tool_turn("帮我造一个 workflow 工具"))

    def test_english_build_tool(self) -> None:
        self.assertTrue(detect_scaffold_tool_turn("build a parser tool for evolve"))

    def test_english_scaffold_phrase(self) -> None:
        self.assertTrue(detect_scaffold_tool_turn("scaffold a new evolved tool under coding"))

    def test_plain_question_negative(self) -> None:
        self.assertFalse(detect_scaffold_tool_turn("what tools do I have?"))


class ExecutorGuardTests(unittest.TestCase):
    def test_tool_toml_requires_base64(self) -> None:
        err = _validate_scaffold_evolved_call(
            ExecutorSession(),
            "write_evolve",
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/x/tool.toml",
                "content": "[tool]\n",
                "arguments": {},
            },
        )
        self.assertIsNotNone(err)
        assert err is not None
        self.assertFalse(err.ok)

    def test_scaffold_allows_staging_write_text(self) -> None:
        err = _validate_scaffold_evolved_call(
            ExecutorSession(scaffold_tool_turn=True),
            "write_text",
            {"tool_name": "write_text", "arguments": {"path": "_staging.toml", "content": "x"}},
        )
        self.assertIsNone(err)

    def test_workspace_project_readme_allowed(self) -> None:
        err = _validate_scaffold_evolved_call(
            ExecutorSession(),
            "write_text",
            {
                "tool_name": "write_text",
                "arguments": {"path": "project1/README.md", "content": "# Project"},
            },
        )
        self.assertIsNone(err)

    def test_evolve_tools_scaffold_path_blocked(self) -> None:
        err = _validate_scaffold_evolved_call(
            ExecutorSession(),
            "write_text",
            {
                "tool_name": "write_text",
                "arguments": {"path": "evolve/tools/common/x/tool.toml", "content": "[tool]"},
            },
        )
        self.assertIsNotNone(err)
        assert err is not None
        self.assertFalse(err.ok)

    def test_registry_reload_flag(self) -> None:
        ok = tool_ok(
            "run_evolved",
            {
                "tool_name": "write_evolve",
                "written": "evolve/tools/common/foo/tool.toml",
            },
        )
        self.assertTrue(
            _write_evolve_wrote_tool_manifest(
                "run_evolved",
                {"tool_name": "write_evolve"},
                ok,
            )
        )
        dry = tool_ok("run_evolved", {"tool_name": "write_evolve", "dry_run": True, "would_write": "x/tool.toml"})
        self.assertFalse(
            _write_evolve_wrote_tool_manifest(
                "run_evolved",
                {"tool_name": "write_evolve", "dry_run": True},
                dry,
            )
        )


class WriteEvolveSkipTests(unittest.TestCase):
    def test_skip_returns_not_ok_when_exists(self) -> None:
        import importlib.util

        from paths import AgentPaths

        paths = AgentPaths.discover()
        probe = paths.evolve / "tools" / "common" / "write_evolve" / "main.py"
        if not probe.is_file():
            self.skipTest("write_evolve missing")

        spec = importlib.util.spec_from_file_location("write_evolve_main", probe)
        assert spec and spec.loader
        we = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(we)

        staging = paths.evolve / "tools" / "common" / "pipeline_skip_test"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "main.py").write_text("print(1)\n", encoding="utf-8")
            rel = "evolve/tools/common/pipeline_skip_test/tool.toml"
            body = (paths.evolve / "tools" / "common" / "run_python" / "tool.toml").read_text(
                encoding="utf-8"
            )
            body = body.replace('name = "run_python"', 'name = "pipeline_skip_test"')
            (staging / "tool.toml").write_text(body, encoding="utf-8")
            result = we.run_write_evolve(
                {
                    "path": rel,
                    "content_base64": base64.b64encode(body.encode()).decode(),
                    "on_conflict": "skip",
                }
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result.get("skipped"))
        finally:
            if staging.is_dir():
                for child in staging.iterdir():
                    child.unlink()
                staging.rmdir()


class CookbookTests(unittest.TestCase):
    def test_staging_step_only_on_scaffold_turn(self) -> None:
        self.assertIn("_staging.toml", format_write_evolve_cookbook(scaffold_turn=True))
        self.assertNotIn("_staging.toml", format_write_evolve_cookbook(scaffold_turn=False))


class RegistryReloadIntegrationTests(unittest.TestCase):
    def test_executor_reload_after_tool_toml(self) -> None:
        from paths import AgentPaths
        from tools.logging import EvolveLog
        from tools.registry import ToolRegistry

        paths = AgentPaths.discover()
        registry = ToolRegistry.load(paths)
        session_dir = paths.data / "sessions" / "_unittest_reload"
        session_dir.mkdir(parents=True, exist_ok=True)
        reloaded: list[str] = []
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(allowed_evolved={"write_evolve"}, session_dir=session_dir),
            confirm_fn=lambda _p, _a: "y",
            evolve_log=EvolveLog(paths.data / "sessions" / "_unittest_reload_log.jsonl"),
        )
        executor.on_registry_reloaded = lambda: reloaded.append("ok")
        demo_dir = paths.evolve / "tools" / "common" / "pipeline_reload_test"
        try:
            self.assertIsNone(registry.get_evolved("pipeline_reload_test"))
            demo_dir.mkdir(parents=True, exist_ok=True)
            (demo_dir / "main.py").write_text('print("reload")\n', encoding="utf-8")
            manifest = (paths.evolve / "tools" / "common" / "run_python" / "tool.toml").read_text(
                encoding="utf-8"
            )
            manifest = manifest.replace('name = "run_python"', 'name = "pipeline_reload_test"')
            result = executor.run(
                "run_evolved",
                {
                    "tool_name": "write_evolve",
                    "path": "evolve/tools/common/pipeline_reload_test/tool.toml",
                    "content_base64": base64.b64encode(manifest.encode()).decode(),
                    "on_conflict": "overwrite",
                    "arguments": {},
                },
            )
            self.assertTrue(result.ok, result.error)
            self.assertIsNotNone(executor.registry.get_evolved("pipeline_reload_test"))
            self.assertEqual(reloaded, ["ok"])
        finally:
            if demo_dir.is_dir():
                for child in demo_dir.iterdir():
                    child.unlink()
                demo_dir.rmdir()


if __name__ == "__main__":
    unittest.main()
