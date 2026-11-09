"""Phase 19 M0: context switch gate tests (T-1902～T-1905)."""

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
from context_switch import (
    build_context_switch_request,
    foreign_workspace_project_write,
    normalize_proposal,
    should_auto_apply_context_switch,
)
from project_cli import parse_project_command, run_project_command
from project_mode import create_project, normalize_project_id, project_dir
from project_switch import read_project_sessions
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths


class ProjectShellSourceContractTests(unittest.TestCase):
    def test_run_turn_source_keeps_project_shell(self) -> None:
        src = inspect.getsource(Agent.run_turn)
        self.assertNotIn('active_shell = "grow"', src)


class ProjectScopeAllowlistTests(unittest.TestCase):
    """F1 belt-and-suspenders: project-bound sessions admit scope=project tools."""

    def test_executor_ensure_admits_project_tool_when_allowlist_stale(self) -> None:
        """validate merges scope=project tools even if allowed_evolved omitted them."""
        paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("project/run_project_tests", "common/write_text"),
        )
        registry = ToolRegistry.load(paths)
        # Stale allowlist: coding/common only — missing project-scoped run_project_tests.
        stale = {t.name for t in registry.session_evolved(["coding"])}
        self.assertNotIn("run_project_tests", stale)

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
            {"tool_name": "run_project_tests", "arguments": {"working_dir": "workspace/demo"}},
        )
        self.assertIn("run_project_tests", executor.session.allowed_evolved or set())
        # Admission itself must succeed; other validation may still fire, but must not be
        # the stale-allowlist "不在本会话清单" rejection.
        if err is not None:
            msg = err.error.message if err.error else str(err)
            self.assertNotIn("不在本会话清单", msg)


class ContextSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        self.pid_a = f"ctx-a-{secrets.token_hex(3)}"
        self.pid_b = f"ctx-b-{secrets.token_hex(3)}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_ctx_{secrets.token_hex(4)}",
        )

    def _bind_project_workspace(self, project_id: str, *, plan_status: str = "draft") -> str:
        from project_cli import bind_project_session

        pid = normalize_project_id(project_id)
        bind_project_session(self.session, pid, plan_status=plan_status)
        self.session.save()
        return pid

    def test_alias_xin_xiangmu(self) -> None:
        cmd = parse_project_command(f"新项目 {self.pid_b}")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.kind, "new")
        self.assertEqual(cmd.project_id, self.pid_b)

    def test_project_new_when_bound_stays_on_current_session(self) -> None:
        """Main home policy: creating another project does not replace the chat session."""
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        from project_cli import bind_project_session

        bind_project_session(self.session, normalize_project_id(self.pid_a), plan_status="draft")
        self.session.save()
        old_cid = self.session.conversation_id
        pid_a = normalize_project_id(self.pid_a)

        outputs: list[str] = []
        result = run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_b}"),
            output_fn=outputs.append,
        )
        self.assertIsNone(result.session)
        self.assertEqual(self.session.conversation_id, old_cid)
        self.assertEqual(self.session.meta.project_id, pid_a)
        self.assertEqual(read_project_sessions(self.paths).get(pid_a), old_cid)
        self.assertEqual(
            read_project_sessions(self.paths).get(normalize_project_id(self.pid_b)),
            old_cid,
        )
        self.assertTrue(project_dir(self.paths, self.pid_b).is_dir())
        self.assertTrue(any("未换线" in line or "仍留在" in line for line in outputs))

    def test_project_new_existing_id_rejects_without_rebinding(self) -> None:
        """A new command must not silently open an existing project."""
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        old_cid = self.session.conversation_id
        pid_a = normalize_project_id(self.pid_a)
        create_project(self.paths, self.pid_b)

        outputs: list[str] = []
        result = run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_b}"),
            output_fn=outputs.append,
        )

        self.assertFalse(result.meta_changed)
        self.assertIsNone(result.session)
        self.assertEqual(self.session.conversation_id, old_cid)
        self.assertEqual(self.session.meta.project_id, "")
        self.assertEqual(read_project_sessions(self.paths).get(pid_a), old_cid)
        self.assertNotIn(normalize_project_id(self.pid_b), read_project_sessions(self.paths))
        self.assertTrue(any(line.startswith("error:") for line in outputs))
        self.assertTrue(any("项目 打开" in line or "项目 切换" in line for line in outputs))

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
        self._bind_project_workspace(self.pid_a)
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

    def test_propose_project_create_auto_applies_without_switch_request(self) -> None:
        """project.create on main home: no 换线 overlay, same session."""
        old_cid = self.session.conversation_id
        events: list[tuple[str, dict]] = []

        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "n",
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
        self.assertFalse(result.data.get("session_replaced"))
        self.assertEqual(result.data.get("session_id"), old_cid)
        self.assertEqual(self.session.meta.project_id, "")
        types = [et for et, _ in events]
        self.assertNotIn("context.switch.request", types)
        self.assertIn("context.switch.done", types)
        self.assertTrue(project_dir(self.paths, self.pid_b).is_dir())
        self.assertEqual(
            read_project_sessions(self.paths).get(normalize_project_id(self.pid_b)),
            old_cid,
        )

    def test_propose_context_switch_reject(self) -> None:
        from project_mode import create_project

        create_project(self.paths, self.pid_a)
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "n",
        )
        result = executor.run(
            "propose_context_switch",
            {"action": "project.switch", "target": self.pid_a},
        )
        self.assertFalse(result.ok)
        self.assertEqual(
            result.error.code if result.error else None,
            ToolErrorCode.CONFIRM_REJECTED,
        )
        self.assertEqual(self.session.meta.project_id, "")

    def test_propose_project_switch_reject_keeps_binding(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_b}"),
            output_fn=lambda _l: None,
        )
        pid_a = self._bind_project_workspace(self.pid_a)
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(self.session.session_dir),
            confirm_fn=lambda _p, _a: "n",
        )
        result = executor.run(
            "propose_context_switch",
            {"action": "project.switch", "target": self.pid_b},
        )
        self.assertFalse(result.ok)
        self.assertEqual(self.session.meta.project_id, pid_a)
        self.assertEqual(self.session.meta.active_shell, "project")

    def test_propose_session_new_on_project_parks_binding(self) -> None:
        """UX-POLISH §7.6 D6/D8: session.new leaves project mapping; new chat is unbound."""
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.pid_a}"),
            output_fn=lambda _l: None,
        )
        pid = self._bind_project_workspace(self.pid_a)
        from project_cli import confirm_project_plan

        confirm_project_plan(self.session)
        self.session.save()
        old_cid = self.session.conversation_id

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
        # Project session stays parked on the old conversation.
        self.assertEqual(read_project_sessions(self.paths).get(pid), old_cid)
        from session import Session

        loaded = Session.load(self.paths, str(new_cid))
        self.assertEqual(loaded.meta.project_id, "")
        self.assertNotEqual(loaded.meta.project_plan_status, "confirmed")

    def test_normalize_shell_switch_rejects_bad_target(self) -> None:
        with self.assertRaises(Exception):
            normalize_proposal(action="shell.switch", target="pet")

    def test_project_create_request_describes_in_place_create(self) -> None:
        proposal = normalize_proposal(
            action="project.create",
            target=self.pid_a,
            reason="quiet create",
        )
        self.assertTrue(should_auto_apply_context_switch(proposal))
        request = build_context_switch_request(self.session, proposal)
        effects = request.get("side_effects") or []
        self.assertTrue(any("未换线" in line or "不换线" in line for line in effects))
        self.assertFalse(any("专用会话" in line for line in effects))


if __name__ == "__main__":
    unittest.main()
