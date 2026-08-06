"""Phase 21 / PROJECT-MODE §0e — progress loop: allowlist, draft shell, inject, overlay."""

from __future__ import annotations

import inspect
import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from loader import format_evolved_catalog_overlay, session_evolved_allowlist
from project_cli import parse_project_command, run_project_command
from project_mode import format_project_overlay, normalize_project_id, project_dir
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths


class ProjectAllowlistTests(unittest.TestCase):
    """IT-60: project shell sees report_progress; grow+coding alone does not."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_text", "project/report_progress"),
        )

    def test_project_shell_includes_report_progress(self) -> None:
        session = create_new(
            self.paths,
            conversation_id=f"_p21_al_{secrets.token_hex(3)}",
        )
        session.meta.topics = ["coding"]
        session.meta.active_shell = "project"
        session.meta.project_root = "workspace/demo"
        session.meta.project_id = "demo"
        session.save()
        allow = session_evolved_allowlist(session, registry=ToolRegistry.load(self.paths))
        self.assertIn("report_progress", allow)
        catalog = format_evolved_catalog_overlay(session, registry=ToolRegistry.load(self.paths))
        # Phase 23 M3: overlay is INDEX (+ hints), not per-tool listing
        self.assertTrue(
            "工具索引" in catalog or "tool-catalog" in catalog or "project.md" in catalog,
            msg=catalog[:200],
        )

    def test_grow_coding_allowlist_includes_report_progress_m1(self) -> None:
        """Phase 23 M1: topic lock removed — active project tools are callable even on grow."""
        session = create_new(
            self.paths,
            conversation_id=f"_p21_grow_{secrets.token_hex(3)}",
        )
        session.meta.topics = ["coding"]
        session.meta.active_shell = "grow"
        session.meta.project_root = ""
        session.meta.project_id = ""
        session.save()
        allow = session_evolved_allowlist(session, registry=ToolRegistry.load(self.paths))
        self.assertIn("report_progress", allow)
        # Catalog listing may still omit project scope until bound / M3 INDEX
        catalog = format_evolved_catalog_overlay(session, registry=ToolRegistry.load(self.paths))
        # F1 overlay listing without project bind: report_progress may be absent from catalog text
        _ = catalog


    def test_executor_ensure_admits_project_tool_when_allowlist_stale(self) -> None:
        """Belt-and-suspenders: validate merges scope=project even if allowlist omitted it."""
        paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("project/report_progress", "common/write_text"),
        )
        registry = ToolRegistry.load(paths)
        # Stale allowlist: coding/common only, missing report_progress (pre-F1 drift).
        stale = {
            t.name
            for t in registry.session_evolved(["coding"])
        }
        self.assertNotIn("report_progress", stale)
        session_dir = paths.data / "sessions" / "_stale_al"
        session_dir.mkdir(parents=True)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(
                session_dir=session_dir,
                allowed_evolved=stale,
                active_shell="project",
                project_root="workspace/demo",
                project_id="demo",
            ),
            confirm_fn=lambda _p, _a: "y",
        )
        err = executor.validate(
            "run_evolved",
            {"tool_name": "report_progress", "arguments": {"summary": "x"}},
        )
        self.assertIn("report_progress", executor.session.allowed_evolved or set())
        # Admission ok even if evidence gate fires (no real project TASKS in this fixture).
        if err is not None:
            msg = err.error.message if err.error else str(err)
            self.assertIn("progress_gate", msg)
            self.assertNotIn("不在本会话清单", msg)

    def test_run_turn_source_keeps_project_shell(self) -> None:
        src = inspect.getsource(Agent.run_turn)
        self.assertNotIn('active_shell = "grow"', src)
        self.assertIn("project_plan_gate_open", src)


class OverlayCopyTests(unittest.TestCase):
    """F5: confirmed overlay mentions report_progress, not bare TASKS writes."""

    def test_confirmed_overlay_mentions_report_progress(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/x",
            project_id="x",
            plan_status="confirmed",
            delivery_profile="ritual",
        )
        self.assertIn("report_progress", overlay)
        self.assertIn("禁止直写 TASKS.md", overlay)
        self.assertNotIn("每步更新 TASKS.md", overlay)


class ReportProgressInjectTests(unittest.TestCase):
    """IT-61 / F4: missing project_id is filled from session."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("project/report_progress", "common/write_text"),
        )
        live = Path(__file__).resolve().parents[2] / "workspace" / "_template"
        dest = self.paths.workspace / "_template"
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("PROJECT.md", "MAP.md", "TASKS.md"):
            src = live / name
            if src.is_file():
                (dest / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        self.project_id = f"p21-{secrets.token_hex(3)}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_p21_inj_{secrets.token_hex(4)}",
        )
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.project_id}"),
            output_fn=lambda _l: None,
        )
        run_project_command(
            self.session,
            self.paths,
            parse_project_command("项目 确认"),
            output_fn=lambda _l: None,
        )
        self.session.meta.project_delivery_profile = "ritual"
        self.session.save()
        self.pid = normalize_project_id(self.project_id)
        tasks = project_dir(self.paths, self.pid) / "TASKS.md"
        # line 2 = first checkbox when header + blank + item
        tasks.write_text(
            "# tasks\n\n- [ ] T-001 skeleton Entity write\n- [ ] T-002 engine Service\n",
            encoding="utf-8",
        )

    def test_inject_project_id_and_arm_stop(self) -> None:
        import os

        os.environ["MY_AGENT_ROOT"] = str(self.paths.agent_root)
        self.addCleanup(lambda: os.environ.pop("MY_AGENT_ROOT", None))

        registry = ToolRegistry.load(self.paths)
        allow = session_evolved_allowlist(self.session, registry=registry)
        self.assertIn("report_progress", allow)

        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(
                self.session.session_dir,
                allowed_evolved=set(allow),
            ),
            confirm_fn=lambda _p, _a: "y",
        )
        executor.session.active_shell = "project"
        executor.session.project_root = f"workspace/{self.pid}"
        executor.session.project_id = self.pid
        executor.session.project_plan_status = "confirmed"
        executor.begin_turn()

        written = executor.run(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {
                    "path": f"workspace/{self.pid}/src/Skeleton.java",
                    "content": "class Skeleton {}",
                    "on_conflict": "overwrite",
                },
            },
        )
        self.assertTrue(written.ok, written.error)

        result = executor.run(
            "run_evolved",
            {
                "tool_name": "report_progress",
                "arguments": {
                    "summary": "did T-001",
                    "task_line": 2,
                },
            },
        )
        self.assertTrue(result.ok, result.error)
        text = (project_dir(self.paths, self.pid) / "TASKS.md").read_text(encoding="utf-8")
        self.assertNotIn("T-001 skeleton Entity write", text)
        archive = (project_dir(self.paths, self.pid) / "TASKS.archive.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("T-001 skeleton Entity write", archive)
        self.assertIn("closed:done", archive)
        self.assertTrue(executor.session.task_stop_armed)

        blocked = executor.run(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {
                    "path": f"workspace/{self.pid}/src/Main.java",
                    "content": "class Main {}",
                    "on_conflict": "overwrite",
                },
            },
        )
        self.assertFalse(blocked.ok)
        self.assertIn("一停", blocked.error.message if blocked.error else "")


if __name__ == "__main__":
    unittest.main()
