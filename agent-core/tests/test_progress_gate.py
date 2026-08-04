"""Phase 24 · Progress Gate — classify + evidence + gates (IT-70～IT-72)."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from progress_gate import (
    classify_task_evidence_kind,
    evidence_satisfies,
    make_evidence_entry,
    report_progress_evidence_block_reason,
    report_progress_repeat_block_reason,
)
from project_cli import parse_project_command, run_project_command
from project_mode import normalize_project_id, project_dir
from session import create_new
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths


class ClassifyEvidenceKindTests(unittest.TestCase):
    """IT-70"""

    def test_write_service_and_page(self) -> None:
        self.assertEqual(
            classify_task_evidence_kind("T-012 必备材料 Service + ServiceImpl"),
            "write",
        )
        self.assertEqual(
            classify_task_evidence_kind("T-014 必备材料前端页面 MaterialList.vue"),
            "write",
        )

    def test_test_phase_line(self) -> None:
        self.assertEqual(
            classify_task_evidence_kind(
                "Phase 4 测试：对必备材料模块进行后端接口与前端联调测试"
            ),
            "test",
        )

    def test_compile_and_build_fe(self) -> None:
        self.assertEqual(classify_task_evidence_kind("T-015 后端可编译通过"), "compile")
        self.assertEqual(classify_task_evidence_kind("T-016 前端可构建通过"), "build_fe")

    def test_verify_db(self) -> None:
        self.assertEqual(
            classify_task_evidence_kind("配置数据库连接并验证联通性（对接数据库）"),
            "verify_db",
        )

    def test_unknown(self) -> None:
        self.assertEqual(classify_task_evidence_kind("随便想想明天做什么"), "unknown")


class EvidenceMatchTests(unittest.TestCase):
    def test_write_needs_write_tool(self) -> None:
        ok, _ = evidence_satisfies(
            "write",
            [make_evidence_entry(tool_name="run_evolved", evolved_name="write_text", ok=True)],
        )
        self.assertTrue(ok)
        ok2, note = evidence_satisfies(
            "write",
            [make_evidence_entry(tool_name="run_evolved", evolved_name="mvn_exec", ok=True)],
        )
        self.assertFalse(ok2)
        self.assertIn("写入", note)

    def test_test_rejects_write_only(self) -> None:
        ok, note = evidence_satisfies(
            "test",
            [make_evidence_entry(tool_name="run_evolved", evolved_name="write_text", ok=True)],
        )
        self.assertFalse(ok)
        self.assertIn("测试", note)

    def test_failed_tool_not_evidence(self) -> None:
        ok, _ = evidence_satisfies(
            "compile",
            [make_evidence_entry(tool_name="run_evolved", evolved_name="mvn_exec", ok=False)],
        )
        self.assertFalse(ok)


class ProgressGateExecutorTests(unittest.TestCase):
    """IT-71 / IT-72"""

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

        self.project_id = f"pg-{secrets.token_hex(3)}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_pg_{secrets.token_hex(4)}",
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
            "# tasks\n\n- [ ] T-001 skeleton Entity write\n- [ ] Phase 1 测试：模块联调测试\n",
            encoding="utf-8",
        )

    def _executor(self) -> ToolExecutor:
        import os

        os.environ["MY_AGENT_ROOT"] = str(self.paths.agent_root)
        self.addCleanup(lambda: os.environ.pop("MY_AGENT_ROOT", None))
        registry = ToolRegistry.load(self.paths)
        allow = {"write_text", "report_progress"}
        executor = ToolExecutor.create(
            paths=self.paths,
            session_dir=self.session.session_dir,
            allowed_evolved=allow,
            confirm_fn=lambda _p, _a: "y",
        )
        executor.registry = registry
        executor.session.active_shell = "project"
        executor.session.project_root = f"workspace/{self.pid}"
        executor.session.project_id = self.pid
        executor.session.project_plan_status = "confirmed"
        executor.session.allowed_evolved = allow
        executor.begin_turn()
        return executor

    def test_it71_reject_report_without_evidence(self) -> None:
        executor = self._executor()
        self.assertEqual(executor.session.armed_task_id, "T-001")
        blocked = executor.run(
            "run_evolved",
            {
                "tool_name": "report_progress",
                "arguments": {"summary": "done", "task_line": 2},
            },
        )
        self.assertFalse(blocked.ok)
        self.assertIn("progress_gate", (blocked.error.message if blocked.error else "") or "")
        text = (project_dir(self.paths, self.pid) / "TASKS.md").read_text(encoding="utf-8")
        self.assertIn("- [ ] T-001", text)

    def test_it71_allow_after_write(self) -> None:
        executor = self._executor()
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
        reported = executor.run(
            "run_evolved",
            {
                "tool_name": "report_progress",
                "arguments": {"summary": "T-001 done", "task_line": 2},
            },
        )
        self.assertTrue(reported.ok, reported.error)
        text = (project_dir(self.paths, self.pid) / "TASKS.md").read_text(encoding="utf-8")
        self.assertNotIn("T-001", text)
        archive = (project_dir(self.paths, self.pid) / "TASKS.archive.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("T-001", archive)
        self.assertIn("closed:done", archive)

    def test_it72_second_report_blocked(self) -> None:
        executor = self._executor()
        executor.run(
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
        first = executor.run(
            "run_evolved",
            {
                "tool_name": "report_progress",
                "arguments": {"summary": "T-001 done", "task_line": 2},
            },
        )
        self.assertTrue(first.ok, first.error)
        self.assertTrue(executor.session.task_stop_armed)
        second = executor.run(
            "run_evolved",
            {
                "tool_name": "report_progress",
                "arguments": {"summary": "also phase test", "task_line": 3},
            },
        )
        self.assertFalse(second.ok)
        self.assertIn(
            "再次 report_progress",
            (second.error.message if second.error else "") or "",
        )

    def test_write_cannot_satisfy_test_kind(self) -> None:
        reason = report_progress_evidence_block_reason(
            active_shell="project",
            armed_task_text="Phase 1 测试：模块联调测试",
            turn_evidence=[
                make_evidence_entry(
                    tool_name="run_evolved", evolved_name="write_text", ok=True
                )
            ],
        )
        self.assertIsNotNone(reason)
        self.assertIn("test", reason or "")

    def test_repeat_helper(self) -> None:
        reason = report_progress_repeat_block_reason(
            active_shell="project",
            task_stop_armed=True,
            tool_name="run_evolved",
            arguments={"tool_name": "report_progress", "arguments": {}},
        )
        self.assertIsNotNone(reason)


if __name__ == "__main__":
    unittest.main()
