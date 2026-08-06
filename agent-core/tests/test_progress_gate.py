"""Phase 24 · Progress Gate — classify + evidence + gates (IT-70～IT-72)."""

from __future__ import annotations

import json
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
    is_progress_gate_tool_error,
    make_evidence_entry,
    PROGRESS_GATE_G9_KERNEL_MESSAGE,
    report_progress_evidence_block_reason,
    report_progress_repeat_block_reason,
)
from project_cli import parse_project_command, run_project_command
from project_mode import normalize_project_id, project_dir
from session import create_new
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import tool_fail

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

    def test_write_colloquial_phase7_style(self) -> None:
        """口语化写码任务须归 write（huiyi T-020/022/024 死锁）。"""
        self.assertEqual(
            classify_task_evidence_kind("T-020 写 SysMenu 菜单列表接口"),
            "write",
        )
        self.assertEqual(classify_task_evidence_kind("T-022 写 City 新增删除"), "write")
        self.assertEqual(classify_task_evidence_kind("T-024 写 SaleSite 全 CRUD"), "write")
        self.assertEqual(
            classify_task_evidence_kind("T-021 改 Layout.vue 动态路由"),
            "write",
        )

    def test_explicit_evidence_tag(self) -> None:
        self.assertEqual(
            classify_task_evidence_kind("确认目录结构完整 [evidence:write]"),
            "write",
        )
        self.assertEqual(
            classify_task_evidence_kind("随便想想 [evidence:compile]"),
            "compile",
        )
        # Tag wins over conflicting colloquial signals
        self.assertEqual(
            classify_task_evidence_kind("写一堆接口但其实要编译 [evidence:compile]"),
            "compile",
        )

    def test_colloquial_does_not_override_test_phase(self) -> None:
        """「接口」出现在联调测试行时仍归 test，不被 write 口语规则抢走。"""
        self.assertEqual(
            classify_task_evidence_kind(
                "Phase 4 测试：对必备材料模块进行后端接口与前端联调测试"
            ),
            "test",
        )


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

    def test_legacy_archived_names_map_to_current_tools(self) -> None:
        """Old turn evidence with archived tool names still satisfies gate."""
        ok_compile, _ = evidence_satisfies(
            "compile",
            [make_evidence_entry(tool_name="run_evolved", evolved_name="mvn_exec", ok=True)],
        )
        self.assertTrue(ok_compile)
        ok_build, _ = evidence_satisfies(
            "build_fe",
            [make_evidence_entry(tool_name="run_evolved", evolved_name="npm_exec", ok=True)],
        )
        self.assertTrue(ok_build)
        ok_write, _ = evidence_satisfies(
            "write",
            [make_evidence_entry(tool_name="run_evolved", evolved_name="append_text", ok=True)],
        )
        self.assertTrue(ok_write)


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
        self.session.meta.project_delivery_profile = "ritual"
        self.session.save()
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
        executor.session.project_delivery_profile = "ritual"
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
        solo_reason = report_progress_repeat_block_reason(
            active_shell="project",
            task_stop_armed=False,
            report_progress_done_this_turn=True,
            tool_name="run_evolved",
            arguments={"tool_name": "report_progress", "arguments": {}},
        )
        self.assertIsNotNone(solo_reason)

    def test_it2406_gate_notice_on_blocked_report(self) -> None:
        """T-2406: progress gate rejection emits structured gate_notice (no force-check)."""
        from tools.executor import ExecutorSession, ToolExecutor
        from tools.registry import ToolRegistry
        from tests.isolation_helpers import temporary_agent_paths

        with temporary_agent_paths(copy_tool_dirs=("project/report_progress",)) as paths:
            proj = paths.workspace / "gate-demo"
            proj.mkdir(parents=True)
            (proj / "TASKS.md").write_text(
                "- [ ] T-001 Phase 1 测试：模块联调测试\n",
                encoding="utf-8",
            )
            registry = ToolRegistry.load(paths)
            events: list[tuple[str, dict]] = []

            def capture(event_type: str, payload: dict) -> None:
                events.append((event_type, payload))

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"report_progress"},
                    project_root="workspace/gate-demo",
                    project_id="gate-demo",
                    active_shell="project",
                ),
                on_event=capture,
            )
            executor.begin_turn()
            blocked = executor.run(
                "run_evolved",
                {
                    "tool_name": "report_progress",
                    "arguments": {"summary": "done"},
                },
            )
            self.assertFalse(blocked.ok)
            evidence = [p for t, p in events if t == "turn.evidence"]
            self.assertTrue(evidence)
            notice = (evidence[-1].get("gate_notice") or "").strip()
            self.assertIn("进度闸门", notice)
            self.assertIn("证据类", notice)
            self.assertIn("无强制勾选", notice)


class SmokeS70ToS74Tests(ProgressGateExecutorTests):
    """T-2408: automated smoke proxies for PROGRESS-GATE §5.2 S-70～S-74."""

    def test_s70_write_evidence_allows_checkbox(self) -> None:
        """S-70: this-turn matched write success → report_progress ok."""
        self.test_it71_allow_after_write()

    def test_s71_no_evidence_rejects(self) -> None:
        """S-71: no this-turn evidence → report_progress blocked, TASKS stays open."""
        self.test_it71_reject_report_without_evidence()

    def test_s72_failed_command_evidence_blocks_test(self) -> None:
        """S-72: confirm-rejected mvn/run_command (ok=false) ≠ test evidence."""
        failed_cmd = [
            make_evidence_entry(
                tool_name="run_evolved",
                evolved_name="run_command",
                ok=False,
            )
        ]
        ritual_reason = report_progress_evidence_block_reason(
            active_shell="project",
            armed_task_text="Phase 1 测试：模块联调测试",
            turn_evidence=failed_cmd,
            delivery_profile="ritual",
        )
        self.assertIsNotNone(ritual_reason)
        self.assertIn("test", ritual_reason or "")

        solo_reason = report_progress_evidence_block_reason(
            active_shell="project",
            armed_task_text="Phase 1 测试：模块联调测试",
            turn_evidence=failed_cmd,
            delivery_profile="solo",
        )
        self.assertIsNotNone(solo_reason)
        self.assertIn("禁止勾选", solo_reason or "")

    def test_s73_second_report_hard_reject(self) -> None:
        """S-73: after successful toggle, second report_progress hard-rejected."""
        self.test_it72_second_report_blocked()

    def test_s74_write_cannot_satisfy_compile_test_build_fe(self) -> None:
        """S-74: write success does not satisfy compile / test / build_fe tasks."""
        write_ok = [
            make_evidence_entry(
                tool_name="run_evolved", evolved_name="write_text", ok=True
            )
        ]
        cases = [
            ("Maven compile 骨架可编译", "compile"),
            ("Phase 1 测试：模块联调测试", "test"),
            ("前端可构建通过", "build_fe"),
        ]
        for title, kind in cases:
            reason = report_progress_evidence_block_reason(
                active_shell="project",
                armed_task_text=title,
                turn_evidence=write_ok,
            )
            self.assertIsNotNone(reason, msg=title)
            self.assertIn(kind, (reason or ""))


class ProgressGateG9KernelTests(unittest.TestCase):
    """T-2410 · G9 kernel notice after report_progress blocked."""

    def test_is_progress_gate_tool_error(self) -> None:
        blocked = tool_fail(
            "run_evolved",
            "validation_error",
            "[progress_gate] no evidence",
            details={"guard_type": "progress_gate_evidence"},
        )
        self.assertTrue(is_progress_gate_tool_error(blocked))
        other = tool_fail(
            "run_evolved",
            "validation_error",
            "bad args",
            details={"guard_type": "task_stop"},
        )
        self.assertFalse(is_progress_gate_tool_error(other))

    def test_it2410_kernel_notice_injected_on_blocked_report(self) -> None:
        from unittest.mock import MagicMock

        from agent import Agent
        from llm_client import LLMResponse
        from tests.isolation_helpers import temporary_agent_paths
        from tools.executor import ExecutorSession, ToolExecutor

        with temporary_agent_paths(copy_tool_dirs=("project/report_progress",)) as paths:
            proj = paths.workspace / "g9-demo"
            proj.mkdir(parents=True)
            (proj / "TASKS.md").write_text(
                "- [ ] T-001 Phase 1 测试：模块联调测试\n",
                encoding="utf-8",
            )
            session = create_new(paths, conversation_id="_it2410_g9")
            session.meta.turn_mode = "agent"
            session.meta.active_shell = "project"
            session.save()

            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    session_dir=session.session_dir,
                    allowed_evolved={"report_progress"},
                    project_root="workspace/g9-demo",
                    project_id="g9-demo",
                    active_shell="project",
                    armed_task_text="- [ ] T-001 Phase 1 测试：模块联调测试",
                ),
            )
            responses = [
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "rp1",
                            "type": "function",
                            "function": {
                                "name": "run_evolved",
                                "arguments": json.dumps(
                                    {
                                        "tool_name": "report_progress",
                                        "arguments": {"summary": "done"},
                                    }
                                ),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content="缺测试证据，已停。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                ),
            ]
            mock_llm = MagicMock()
            mock_llm.chat.side_effect = responses

            agent = Agent(session=session, executor=executor, llm=mock_llm)
            agent._run_parent_tool_loop(
                max_rounds=5,
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "run_evolved"},
                    }
                ],
                model="test",
                segment_start_index=len(session.messages),
            )

            user_blob = "\n".join(
                str(m.get("content", ""))
                for m in session.messages
                if m.get("role") == "user"
            )
            self.assertIn(PROGRESS_GATE_G9_KERNEL_MESSAGE, user_blob)


if __name__ == "__main__":
    unittest.main()
