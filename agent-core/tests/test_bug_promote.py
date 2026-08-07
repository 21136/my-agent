"""IT-543 · bug_promote sidebar flow (T-5403)."""

from __future__ import annotations

import secrets
import unittest
from unittest.mock import patch

from plan_agent import drop_plan_agent, get_plan_agent
from project_mode import (
    create_project,
    normalize_project_id,
    project_dir,
    resolve_bug_promote_phase,
)
from subagent import extract_review_blocker_items
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry

from tests.isolation_helpers import make_temp_agent_paths, temporary_agent_paths


class ExtractReviewBlockerTests(unittest.TestCase):
    def test_parses_p0_lines(self) -> None:
        items = extract_review_blocker_items(
            "P0: init.sql 缺表\nP1: 登录接口 500\nREVIEW_VERDICT: fail",
            verdict="fail",
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["severity"], "P0")
        self.assertEqual(items[0]["title"], "init.sql 缺表")

    def test_fail_fallback_when_no_marker_lines(self) -> None:
        items = extract_review_blocker_items(
            "数据库迁移未执行\nREVIEW_VERDICT: fail",
            verdict="fail",
        )
        self.assertEqual(len(items), 1)
        self.assertIn("数据库", items[0]["title"])


class ResolveBugPromotePhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"bp-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        self.tasks = project_dir(self.paths, self.pid) / "TASKS.md"

    def test_prefers_bugs_section_when_present(self) -> None:
        self.tasks.write_text(
            "## Phase A\n"
            "- [ ] T-001 open task with enough text\n"
            "## Bugs\n"
            "- [ ] T-900 old bug task with enough text\n",
            encoding="utf-8",
        )
        self.assertEqual(resolve_bug_promote_phase(self.paths, self.pid), "Bugs")

    def test_uses_current_open_phase_without_bugs_section(self) -> None:
        self.tasks.write_text(
            "## Phase A\n"
            "- [x] T-001 done task with enough text\n"
            "## Phase B\n"
            "- [ ] T-002 open task with enough text\n",
            encoding="utf-8",
        )
        self.assertEqual(resolve_bug_promote_phase(self.paths, self.pid), "Phase B")


class BugPromoteFlowTests(unittest.TestCase):
    """IT-543 · deliverable_review fail → adopt into TASKS."""

    def test_it543_review_fail_emits_bug_promote_and_accept_writes_tasks(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text",),
        ) as paths:
            pid = normalize_project_id(f"it543-{secrets.token_hex(3)}")
            proj = paths.workspace / pid
            proj.mkdir(parents=True)
            (proj / "TASKS.md").write_text(
                "## Phase A\n- [ ] T-001 open task with enough text\n",
                encoding="utf-8",
            )
            (proj / "MAP.md").write_text("# map\n", encoding="utf-8")
            (proj / "PROJECT.md").write_text("# proj\n", encoding="utf-8")

            from session import create_new
            from subagent import SubagentResult

            session = create_new(paths, conversation_id=f"it543_{pid}")
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.active_shell = "project"
            session.meta.project_plan_status = "confirmed"
            session.save()

            registry = ToolRegistry.load(paths)
            exec_session = ToolExecutor.create(
                paths=paths,
                session_dir=session.session_dir,
                allowed_evolved=set(),
                confirm_fn=lambda _p, _a: "y",
            ).session
            executor = ToolExecutor(
                registry=registry,
                session=exec_session,
                confirm_fn=lambda _p, _a: "y",
            )
            executor.session.project_id = pid
            executor.session.project_root = f"workspace/{pid}"
            executor.session.active_shell = "project"

            mock_result = SubagentResult(
                kind="review",
                summary="P0: init.sql 缺表 users\nREVIEW_VERDICT: fail",
                paths_cited=[],
                tool_rounds=1,
                truncated=False,
                task="验收数据库",
                verdict="fail",
            )
            with patch(
                "subagent.SubagentRunner.run_deliverable_review",
                return_value=mock_result,
            ):
                result = executor.run(
                    "deliverable_review",
                    {"task": "验收数据库", "scope": "full"},
                )
            self.assertTrue(result.ok, result.error)

            drop_plan_agent(pid)
            agent = get_plan_agent(paths, pid)
            bug_cards = [
                s
                for s in agent._pending_gated.values()
                if s.get("kind") == "bug_promote"
            ]
            self.assertEqual(len(bug_cards), 1)
            self.assertEqual(bug_cards[0].get("action"), "add_task")

            state = agent.build_state(session)
            kinds = [str(s.get("kind")) for s in state.get("suggestions", [])]
            self.assertIn("bug_promote", kinds)

            adopt = agent.accept_suggestion(bug_cards[0]["id"])
            self.assertTrue(adopt.get("ok"))
            tasks_text = (proj / "TASKS.md").read_text(encoding="utf-8")
            self.assertIn("init.sql 缺表 users", tasks_text)
            self.assertIn("- [ ]", tasks_text)

    def test_ritual_filters_p2_blockers(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"bp2-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        agent = get_plan_agent(self.paths, self.pid)
        emitted = agent.emit_bug_promote_from_review(
            "P2: cosmetic copy issue\nP0: auth broken\nREVIEW_VERDICT: fail",
            source="deliverable_review",
            delivery_profile="ritual",
            verdict="fail",
        )
        self.assertEqual(len(emitted), 1)
        payload = emitted[0].get("payload") or {}
        self.assertEqual(payload.get("severity"), "P0")


if __name__ == "__main__":
    unittest.main()
