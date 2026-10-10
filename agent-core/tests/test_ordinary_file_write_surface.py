"""Ordinary file edits use flat write tools and the card names the file."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import format_capability_hints, format_session_evolved_catalog
from project_mode import create_project
from tests.isolation_helpers import temporary_agent_paths
from tool_proxies import rewrite_proxy_tool_call
from tools.builtin.run_evolved import coalesce_tool_arguments
from tools.executor import (
    ExecutorSession,
    ToolExecutor,
    _tool_result_summary,
)
from tools.registry import ToolRegistry
from tools.schema import tool_ok


class OrdinaryFileWritePromptTests(unittest.TestCase):
    def test_core_tells_model_to_call_flat_write_tools(self) -> None:
        core = (_AGENT_CORE / "prompts" / "core.txt").read_text(encoding="utf-8")
        table = core.split("## Execution boundary", 1)[1].split("Forbidden:", 1)[0]
        self.assertIn("flat `write_text`", table)
        self.assertIn("flat `patch_file`", table)
        self.assertIn("不要**套 `run_evolved`", table)
        self.assertNotIn("`run_evolved` → `write_text`", table)
        self.assertNotIn("`run_evolved` → `run_command`", table)
        self.assertLess(len(core.splitlines()), 120)
        self.assertIn("read_file` it in **this** turn", core)
        self.assertIn("not expected to exist yet does not need that read", core)
        self.assertIn("edit an existing file with `patch_file`", core)
        self.assertIn("reserve `write_text` for a new file or a near-total rewrite", core)
        self.assertIn("Do not `read_file` `evolve/tool-catalog/buckets/`", core)
        coding = (_ROOT / "evolve" / "prompts" / "coding.md").read_text(encoding="utf-8")
        self.assertIn("先读再断言", coding)
        self.assertIn("本回合 `read_file`", coding)
        self.assertIn("不必先 `read_file` 探一次", coding)
        self.assertIn("读完之后改已有文件仍用 `patch_file`", coding)
        self.assertIn("不要为 `read_file` / `write_text` / `patch_file` / `run_command`", coding)

    def test_catalog_does_not_send_basic_tools_to_bucket_notes(self) -> None:
        index = (_ROOT / "evolve" / "tool-catalog" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("不要**为它们读 bucket", index)
        self.assertIn("patch_file", index)
        self.assertLess(len(index), 2048)
        write_row = next(line for line in index.splitlines() if "buckets/write.md" in line)
        run_row = next(line for line in index.splitlines() if "buckets/run.md" in line)
        self.assertNotIn("write_text", write_row)
        self.assertNotIn("patch_file", write_row)
        self.assertNotIn("run_command", run_row)

        class _Meta:
            topics: list[str] = []

        class _Session:
            meta = _Meta()

        hints = format_capability_hints(_Session())
        self.assertIn("不要读 bucket", hints)
        self.assertIn("已有文件用 patch_file", hints)
        self.assertIn("write_text 只用于新建或几乎整文件重写", hints)
        self.assertNotIn("细节 `read_file evolve/tool-catalog/buckets/<桶>.md`。执行面", hints)

        with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
            registry = ToolRegistry.load(paths)
            catalog = format_session_evolved_catalog(["coding"], registry=registry)
        write_line = next(line for line in catalog.splitlines() if line.startswith("- write_text:"))
        patch_line = next(line for line in catalog.splitlines() if line.startswith("- patch_file:"))
        self.assertIn("改已有文件用 patch_file", write_line)
        self.assertIn("不要改用 write_text", patch_line)

    def test_evolved_catalog_does_not_nest_flat_writes(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
            registry = ToolRegistry.load(paths)
            catalog = format_session_evolved_catalog(["coding"], registry=registry)
        self.assertIn("不要套 run_evolved", catalog)
        self.assertNotIn("调用 run_evolved.tool_name", catalog)
        write_line = next(line for line in catalog.splitlines() if line.startswith("- write_text:"))
        self.assertIn("扁平原语", write_line)


class FileChangeSummaryTests(unittest.TestCase):
    def test_written_path_is_not_bare_ok(self) -> None:
        result = tool_ok(
            "run_evolved",
            {
                "tool_name": "write_text",
                "written": "workspace/scratch-readme/README.md",
                "path": "workspace/scratch-readme/README.md",
                "change_summary": "写入 1 行：一句项目简介",
                "skipped": False,
            },
        )
        summary = _tool_result_summary(result)
        self.assertIn("workspace/scratch-readme/README.md", summary)
        self.assertIn("一句项目简介", summary)
        self.assertNotEqual(summary, "ok")

    def test_coalesce_lifts_top_level_write_path(self) -> None:
        inner = coalesce_tool_arguments(
            {
                "tool_name": "write_text",
                "path": "workspace/scratch-readme/README.md",
                "content": "一句项目简介\n",
                "arguments": {},
            }
        )
        self.assertEqual(inner["path"], "workspace/scratch-readme/README.md")
        self.assertIn("项目简介", inner["content"])


class OrdinaryFileWriteCardTests(unittest.TestCase):
    def _executor(self, paths, events: list[tuple[str, dict[str, Any]]]) -> ToolExecutor:
        registry = ToolRegistry.load(paths)
        confirmed: list[str] = []

        def _confirm(tool_name: str, allow_approve_all: bool) -> str:
            confirmed.append(tool_name)
            return "n"

        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(
                allowed_evolved={"write_text", "patch_file"},
                project_root="workspace/scratch-readme",
                project_id="scratch-readme",
                active_shell="project",
                project_plan_status="confirmed",
                project_workflow_stage="implementation",
                project_entry="direct",
                runaway_enabled=False,
            ),
            confirm_fn=_confirm,
            on_event=lambda kind, payload: events.append((kind, payload)),
        )
        self.addCleanup(lambda: self.assertEqual(confirmed, [], "safe in-project write asked to confirm"))
        return executor

    def test_flat_write_card_names_file_without_confirm(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            create_project(paths, "scratch-readme", project_template="light")
            events: list[tuple[str, dict[str, Any]]] = []
            executor = self._executor(paths, events)
            name, args = rewrite_proxy_tool_call(
                "write_text",
                {
                    "path": "workspace/scratch-readme/README.md",
                    "content": "一句项目简介\n",
                    "on_conflict": "overwrite",
                },
            )
            result = executor.run(name, args)
            self.assertTrue(result.ok, result.error)
            written = paths.workspace / "scratch-readme" / "README.md"
            self.assertEqual(written.read_text(encoding="utf-8"), "一句项目简介\n")
            start = next(payload for kind, payload in events if kind == "tool.start")
            end = next(payload for kind, payload in events if kind == "tool.end")
            self.assertEqual(start["tool"], "write_text")
            self.assertIn("README.md", start["summary"])
            self.assertEqual(end["tool"], "write_text")
            self.assertIn("README.md", end["summary"])
            self.assertIn("一句项目简介", end["summary"])
            self.assertNotEqual(end["summary"], "ok")

    def test_run_evolved_write_card_still_names_file(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            create_project(paths, "scratch-readme", project_template="light")
            events: list[tuple[str, dict[str, Any]]] = []
            executor = self._executor(paths, events)
            result = executor.run(
                "run_evolved",
                {
                    "tool_name": "write_text",
                    "path": "workspace/scratch-readme/README.md",
                    "content": "一句项目简介\n",
                    "on_conflict": "overwrite",
                    "arguments": {},
                },
            )
            self.assertTrue(result.ok, result.error)
            end = next(payload for kind, payload in events if kind == "tool.end")
            self.assertEqual(end["tool"], "write_text")
            self.assertIn("workspace/scratch-readme/README.md", end["summary"])
            self.assertNotEqual(end["summary"], "ok")


if __name__ == "__main__":
    unittest.main()
