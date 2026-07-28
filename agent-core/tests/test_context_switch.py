"""Phase 19 M0: context switch gate tests (T-1902～T-1905)."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from context_switch import (
    foreign_workspace_project_write,
    normalize_proposal,
)
from project_cli import parse_project_command, run_project_command
from project_mode import normalize_project_id, project_dir
from project_switch import read_project_sessions
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths


class ContextSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        self.pid_a = f"ctx-a-{secrets.token_hex(3)}"
        self.pid_b = f"ctx-b-{secrets.token_hex(3)}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_ctx_{secrets.token_hex(4)}",
        )

    def test_alias_xin_xiangmu(self) -> None:
        cmd = parse_project_command(f"新项目 {self.pid_b}")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.kind, "new")
        self.assertEqual(cmd.project_id, self.pid_b)

    def test_project_new_when_bound_replaces_session(self) -> None:
        """T-1904: already bound → new project gets a new conversation_id."""
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        self.assertEqual(self.session.meta.project_id, normalize_project_id(self.pid_a))
        old_cid = self.session.conversation_id

        outputs: list[str] = []
        result = run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_b}"),
            output_fn=outputs.append,
        )
        self.assertIsNotNone(result.session)
        assert result.session is not None
        self.assertNotEqual(result.session.conversation_id, old_cid)
        self.assertEqual(result.session.meta.project_id, normalize_project_id(self.pid_b))
        self.assertEqual(
            read_project_sessions(self.paths).get(normalize_project_id(self.pid_a)),
            old_cid,
        )
        self.assertEqual(
            read_project_sessions(self.paths).get(normalize_project_id(self.pid_b)),
            result.session.conversation_id,
        )
        self.assertTrue(project_dir(self.paths, self.pid_b).is_dir())

    def test_foreign_write_path_detection(self) -> None:
        self.assertEqual(
            foreign_workspace_project_write(
                "java-doudizhu/PROJECT.md",
                current_project_root="workspace/stab-r1-demo",
            ),
            "java-doudizhu",
        )
        self.assertIsNone(
            foreign_workspace_project_write(
                "stab-r1-demo/TASKS.md",
                current_project_root="workspace/stab-r1-demo",
            )
        )
        self.assertIsNone(
            foreign_workspace_project_write(
                "workspace/stab-r1-demo/src/Main.java",
                current_project_root="workspace/stab-r1-demo",
            )
        )

    def test_executor_blocks_foreign_project_write(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        # confirm plan so write gate is not plan_draft
        from project_cli import confirm_project_plan

        # Need TASKS already from template
        confirm_project_plan(self.session)
        self.session.save()

        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "y",
        )
        result = executor.validate(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {
                    "path": f"{normalize_project_id(self.pid_b)}/PROJECT.md",
                    "content": "# other\n",
                    "on_conflict": "overwrite",
                },
            },
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code if result.error else None, ToolErrorCode.VALIDATION_ERROR)
        self.assertIn("propose_context_switch", result.error.message if result.error else "")

    def test_propose_context_switch_confirm_y(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        old_cid = self.session.conversation_id
        events: list[tuple[str, dict]] = []

        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "y",
            on_event=lambda et, payload: events.append((et, payload)),
        )
        result = executor.run(
            "propose_context_switch",
            {
                "action": "project.create",
                "target": self.pid_b,
                "reason": "test create",
            },
        )
        self.assertTrue(result.ok, result.error.message if result.error else "")
        assert isinstance(result.data, dict)
        self.assertTrue(result.data.get("session_replaced"))
        self.assertNotEqual(result.data.get("session_id"), old_cid)
        types = [et for et, _ in events]
        self.assertIn("context.switch.request", types)
        self.assertIn("context.switch.done", types)

    def test_propose_context_switch_reject(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "n",
        )
        result = executor.run(
            "propose_context_switch",
            {"action": "project.create", "target": self.pid_b},
        )
        self.assertFalse(result.ok)
        self.assertEqual(
            result.error.code if result.error else None,
            ToolErrorCode.CONFIRM_REJECTED,
        )
        self.assertEqual(self.session.meta.project_id, normalize_project_id(self.pid_a))
        # rejected create must not rebind current session
        self.assertFalse(
            read_project_sessions(self.paths).get(normalize_project_id(self.pid_b))
            == self.session.conversation_id
        )

    def test_propose_shell_switch_to_grow(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        old_cid = self.session.conversation_id
        # seed a grow line
        from shell_switch import record_shell_session
        from session import create_new

        grow = create_new(self.paths, conversation_id=f"_grow_{secrets.token_hex(3)}")
        grow.meta.active_shell = "grow"
        grow.save()
        record_shell_session(self.paths, "grow", grow.conversation_id)

        events: list[tuple[str, dict]] = []
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "y",
            on_event=lambda et, payload: events.append((et, payload)),
        )
        result = executor.run(
            "propose_context_switch",
            {
                "action": "shell.switch",
                "target": "grow",
                "reason": "go evolve",
            },
        )
        self.assertTrue(result.ok, result.error.message if result.error else "")
        assert isinstance(result.data, dict)
        self.assertEqual(result.data.get("session_id"), grow.conversation_id)
        self.assertTrue(result.data.get("session_replaced"))
        self.assertNotEqual(result.data.get("session_id"), old_cid)
        types = [et for et, _ in events]
        self.assertIn("context.switch.request", types)
        self.assertIn("context.switch.done", types)
        self.assertIn("shell.switch.done", types)

    def test_propose_shell_switch_reject(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "n",
        )
        result = executor.run(
            "propose_context_switch",
            {"action": "shell.switch", "target": "daily"},
        )
        self.assertFalse(result.ok)
        self.assertEqual(self.session.meta.project_id, normalize_project_id(self.pid_a))
        self.assertEqual(self.session.meta.active_shell, "project")

    def test_propose_session_new_on_grow(self) -> None:
        from shell_switch import record_shell_session, read_shell_sessions

        self.session.meta.active_shell = "grow"
        self.session.save()
        record_shell_session(self.paths, "grow", self.session.conversation_id)
        old_cid = self.session.conversation_id

        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "y",
        )
        result = executor.run(
            "propose_context_switch",
            {
                "action": "session.new",
                "target": "current",
                "reason": "clean slate",
            },
        )
        self.assertTrue(result.ok, result.error.message if result.error else "")
        assert isinstance(result.data, dict)
        self.assertTrue(result.data.get("session_replaced"))
        new_cid = result.data.get("session_id")
        self.assertNotEqual(new_cid, old_cid)
        self.assertEqual(read_shell_sessions(self.paths).get("grow"), new_cid)

    def test_propose_session_new_on_project_keeps_binding(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        from project_cli import confirm_project_plan

        confirm_project_plan(self.session)
        self.session.save()
        old_cid = self.session.conversation_id
        pid = normalize_project_id(self.pid_a)

        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "y",
        )
        result = executor.run(
            "propose_context_switch",
            {"action": "session.new", "target": "project"},
        )
        self.assertTrue(result.ok, result.error.message if result.error else "")
        assert isinstance(result.data, dict)
        new_cid = result.data.get("session_id")
        self.assertNotEqual(new_cid, old_cid)
        self.assertEqual(read_project_sessions(self.paths).get(pid), new_cid)
        from session import Session

        loaded = Session.load(self.paths, str(new_cid))
        self.assertEqual(loaded.meta.project_id, pid)
        self.assertEqual(loaded.meta.project_plan_status, "confirmed")

    def test_session_new_rejects_cross_shell_target(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "y",
        )
        result = executor.run(
            "propose_context_switch",
            {"action": "session.new", "target": "grow"},
        )
        self.assertFalse(result.ok)
        self.assertIn("当前壳", result.error.message if result.error else "")

    def test_normalize_shell_switch_rejects_bad_target(self) -> None:
        with self.assertRaises(Exception):
            normalize_proposal(action="shell.switch", target="pet")


if __name__ == "__main__":
    unittest.main()
