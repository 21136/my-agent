"""Cross-session read_file confirm (T-1117) and shell session isolation (T-1116 / T-1804-04)."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_cli import bind_project_session
from project_mode import create_project, normalize_project_id
from project_switch import read_project_sessions, record_project_session
from session import Session, create_new
from shell_switch import (
    cross_session_read_target,
    read_shell_sessions,
    record_shell_session,
    switch_shell,
)
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths, temporary_agent_paths


def test_cross_session_read_target_detects_other_session() -> None:
    with temporary_agent_paths() as paths:
        a = create_new(paths, conversation_id="_test_cross_sess_a")
        b = create_new(paths, conversation_id="_test_cross_sess_b")
        target = cross_session_read_target(
            paths,
            a.conversation_id,
            f"data/sessions/{b.conversation_id}/messages.jsonl",
        )
        assert target == b.conversation_id
        assert (
            cross_session_read_target(
                paths,
                a.conversation_id,
                f"data/sessions/{a.conversation_id}/messages.jsonl",
            )
            is None
        )


def test_read_file_other_session_requires_confirm() -> None:
    with temporary_agent_paths() as paths:
        registry = ToolRegistry.load(paths)
        a = create_new(paths, conversation_id="_test_cross_confirm_a")
        b = create_new(paths, conversation_id="_test_cross_confirm_b")
        confirms: list[str] = []

        def confirm_fn(_preview: str, _allow: bool) -> str:
            confirms.append("asked")
            return "n"

        executor = ToolExecutor(
            registry=registry,
            session=executor_session_from(a),
            confirm_fn=confirm_fn,
        )
        result = executor.run(
            "read_file",
            {"path": f"data/sessions/{b.conversation_id}/messages.jsonl"},
        )
        assert confirms == ["asked"]
        assert not result.ok


def executor_session_from(session):
    from tools.executor import ExecutorSession

    return ExecutorSession.load(session.session_dir)


class CrossSessionReadConfirmTests(unittest.TestCase):
    """T-1804-07 / IT-17 / T-1117: non-current session read_file/grep must confirm."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        token = secrets.token_hex(4)
        self.session_a_id = f"_test_cross_read_a_{token}"
        self.session_b_id = f"_test_cross_read_b_{token}"
        self.session_a = create_new(self.paths, conversation_id=self.session_a_id)
        self.session_b = create_new(self.paths, conversation_id=self.session_b_id)
        self.registry = ToolRegistry.load(self.paths)

    def _executor(self, session: Session, confirm_fn) -> ToolExecutor:
        return ToolExecutor(
            registry=self.registry,
            session=executor_session_from(session),
            confirm_fn=confirm_fn,
        )

    def test_same_session_read_skips_confirm(self) -> None:
        confirms: list[str] = []
        executor = self._executor(
            self.session_a,
            lambda _preview, _allow: confirms.append("asked") or "y",
        )
        own_path = f"data/sessions/{self.session_a_id}/messages.jsonl"
        result = executor.run("read_file", {"path": own_path})
        self.assertTrue(result.ok)
        self.assertEqual(confirms, [])

    def test_other_session_read_requires_confirm_and_rejects(self) -> None:
        previews: list[str] = []
        confirms: list[str] = []

        def confirm_fn(preview: str, _allow: bool) -> str:
            previews.append(preview)
            confirms.append("asked")
            return "n"

        executor = self._executor(self.session_a, confirm_fn)
        target = f"data/sessions/{self.session_b_id}/messages.jsonl"
        result = executor.run("read_file", {"path": target})
        self.assertEqual(confirms, ["asked"])
        self.assertFalse(result.ok)
        self.assertEqual(
            result.error.code if result.error else "",
            ToolErrorCode.CONFIRM_REJECTED,
        )
        self.assertIn(self.session_b_id, previews[0])
        self.assertIn("Cross-session peek", previews[0])

    def test_other_session_read_succeeds_when_confirmed(self) -> None:
        confirms: list[str] = []
        executor = self._executor(
            self.session_a,
            lambda _preview, _allow: confirms.append("y") or "y",
        )
        target = f"data/sessions/{self.session_b_id}/messages.jsonl"
        result = executor.run("read_file", {"path": target})
        self.assertEqual(confirms, ["y"])
        self.assertTrue(result.ok)

    def test_grep_other_session_requires_confirm(self) -> None:
        confirms: list[str] = []
        executor = self._executor(
            self.session_a,
            lambda _preview, _allow: confirms.append("asked") or "n",
        )
        target = f"data/sessions/{self.session_b_id}"
        result = executor.run("grep", {"path": target, "pattern": "role"})
        self.assertEqual(confirms, ["asked"])
        self.assertFalse(result.ok)
        self.assertEqual(
            result.error.code if result.error else "",
            ToolErrorCode.CONFIRM_REJECTED,
        )


class ShellSessionIsolationTests(unittest.TestCase):
    """T-1804-04 / T-1116: grow · daily · project keep separate conversation_id lines."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        token = secrets.token_hex(4)
        self.grow_id = f"_test_shell_grow_{token}"
        self.daily_id = f"_test_shell_daily_{token}"
        self.proj_sess_id = f"_test_shell_proj_{token}"
        self.project_id = f"test-shell-{token}"
        self._session_ids = [self.grow_id, self.daily_id, self.proj_sess_id]

    def _seed_shell_sessions(self) -> tuple[Session, Session, Session]:
        grow = create_new(self.paths, conversation_id=self.grow_id)
        grow.meta.active_shell = "grow"
        grow.save()
        record_shell_session(self.paths, "grow", grow.conversation_id)

        daily = create_new(self.paths, conversation_id=self.daily_id)
        daily.meta.active_shell = "daily"
        daily.save()
        record_shell_session(self.paths, "daily", daily.conversation_id)

        create_project(self.paths, self.project_id)
        proj = create_new(self.paths, conversation_id=self.proj_sess_id)
        bind_project_session(proj, self.project_id, plan_status="draft")
        proj.save()
        record_project_session(self.paths, self.project_id, proj.conversation_id)
        proj.meta.active_shell = "project"
        proj.save()

        return grow, daily, proj

    def test_grow_daily_project_have_distinct_conversation_ids(self) -> None:
        grow, daily, proj = self._seed_shell_sessions()
        ids = {grow.conversation_id, daily.conversation_id, proj.conversation_id}
        self.assertEqual(len(ids), 3)

        mapping = read_shell_sessions(self.paths)
        self.assertEqual(mapping.get("grow"), grow.conversation_id)
        self.assertEqual(mapping.get("daily"), daily.conversation_id)
        self.assertEqual(
            read_project_sessions(self.paths).get(normalize_project_id(self.project_id)),
            proj.conversation_id,
        )

        from_project, replaced = switch_shell(self.paths, proj, "grow")
        self.assertTrue(replaced)
        self.assertEqual(from_project.conversation_id, grow.conversation_id)
        self.assertEqual(from_project.meta.active_shell, "grow")
        self.assertFalse(from_project.meta.project_id)

        from_grow, replaced_daily = switch_shell(self.paths, from_project, "daily")
        self.assertTrue(replaced_daily)
        self.assertEqual(from_grow.conversation_id, daily.conversation_id)
        self.assertEqual(from_grow.meta.active_shell, "daily")

        back_project, replaced_project = switch_shell(
            self.paths,
            from_grow,
            "project",
            project_id=self.project_id,
        )
        self.assertTrue(replaced_project)
        self.assertEqual(back_project.conversation_id, proj.conversation_id)
        self.assertEqual(back_project.meta.active_shell, "project")
        self.assertEqual(back_project.meta.project_id, normalize_project_id(self.project_id))

    def test_cross_session_read_across_shell_lines(self) -> None:
        grow, daily, proj = self._seed_shell_sessions()
        self.assertEqual(
            cross_session_read_target(
                self.paths,
                grow.conversation_id,
                f"data/sessions/{proj.conversation_id}/messages.jsonl",
            ),
            proj.conversation_id,
        )
        self.assertEqual(
            cross_session_read_target(
                self.paths,
                daily.conversation_id,
                f"data/sessions/{grow.conversation_id}/messages.jsonl",
            ),
            grow.conversation_id,
        )
        self.assertIsNone(
            cross_session_read_target(
                self.paths,
                grow.conversation_id,
                f"data/sessions/{grow.conversation_id}/messages.jsonl",
            )
        )


if __name__ == "__main__":
    unittest.main()
