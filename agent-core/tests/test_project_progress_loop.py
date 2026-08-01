"""Phase 21 / PROJECT-MODE §0e — progress loop smoke (allowlist + inject + gate).

Note: full Phase 21 suite lived here; file was wiped in working tree and rebuilt
minimal for Phase 24 compatibility (evidence before report_progress).
"""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import session_evolved_allowlist
from project_cli import parse_project_command, run_project_command
from project_mode import format_project_overlay, normalize_project_id, project_dir
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths


class ProjectAllowlistTests(unittest.TestCase):
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


class OverlayCopyTests(unittest.TestCase):
    def test_confirmed_overlay_mentions_report_progress(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/x",
            project_id="x",
            plan_status="confirmed",
            next_open_task="- [ ] T-001 Entity",
            armed_task_id="T-001",
        )
        self.assertIn("report_progress", overlay)
        self.assertIn("对口工具成功证据", overlay)


class ReportProgressInjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_text", "project/report_progress"),
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
            conversation_id=f"_p21_{secrets.token_hex(4)}",
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
        self.pid = normalize_project_id(self.project_id)
        tasks = project_dir(self.paths, self.pid) / "TASKS.md"
        tasks.write_text(
            "# tasks\n\n- [ ] T-001 skeleton Entity\n- [ ] T-002 engine Service\n",
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
                    "path": f"workspace/{self.pid}/src/A.java",
                    "content": "class A {}",
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
        self.assertIn("- [x] T-001 skeleton Entity", text)
        self.assertTrue(executor.session.task_stop_armed)


if __name__ == "__main__":
    unittest.main()
