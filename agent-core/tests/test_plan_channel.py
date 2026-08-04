"""Phase 38 → 39 migration: Plan channel internals (deprecated dual-bubble assertions removed)."""

from __future__ import annotations

import json
import secrets
import unittest
from unittest.mock import MagicMock

from plan_agent import clear_plan_chat_on_enter, drop_plan_agent, get_plan_agent
from plan_tools import (
    classify_plan_tool,
    execute_plan_tool,
    is_plan_domain_write_target,
)
from project_api import dispatch_plan_user_message
from project_mode import create_project, normalize_project_id, project_dir
from session import create_new

from tests.isolation_helpers import make_temp_agent_paths


class PlanChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"p38-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.tasks = self.root / "TASKS.md"

    def tearDown(self) -> None:
        drop_plan_agent(self.pid)

    def _session(self):
        session = create_new(
            self.paths,
            conversation_id=f"_p38_{secrets.token_hex(3)}",
        )
        session.meta.project_id = self.pid
        session.meta.active_shell = "project"
        session.messages = [
            {"role": "user", "content": "MAIN_CHAT_SECRET_SHOULD_NOT_LEAK"},
            {"role": "assistant", "content": "main agent long reply with tools"},
        ]
        return session

    def test_s192_enter_project_clears_plan_transcript(self) -> None:
        self.agent.append_plan_turn("user", "先前计划聊")
        self.agent.append_plan_turn("assistant", "先前回复")
        self.agent.set_partner_notices("旧告知")
        self.assertEqual(len(self.agent.plan_transcript_snapshot()), 2)

        clear_plan_chat_on_enter(self.paths, self.pid)
        agent = get_plan_agent(self.paths, self.pid)
        self.assertEqual(agent.plan_transcript_snapshot(), [])
        self.assertEqual(agent._last_partner_notices, [])
        self.assertTrue(self.tasks.is_file())

    def test_it190_plan_context_excludes_main_messages(self) -> None:
        """IT-190: Plan prompt assembly must not ingest main chat transcript."""
        self.agent.append_plan_turn("user", "plan-only-turn")
        audit = self.agent.build_context_messages_for_audit()
        blob = json.dumps(audit, ensure_ascii=False)
        self.assertIn("plan-only-turn", blob)
        self.assertNotIn("MAIN_CHAT_SECRET_SHOULD_NOT_LEAK", blob)

        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps(
                {"reply": "只谈计划", "operations": [], "tool_calls": []},
                ensure_ascii=False,
            )
        )
        mock_llm._plan_model = "deepseek-v4-flash"
        self.agent._llm = mock_llm
        self.agent.reason_about_intent("优化下计划")
        call_args = mock_llm.chat.call_args
        self.assertIsNotNone(call_args)
        messages = call_args[0][0]
        user_content = messages[1]["content"]
        self.assertNotIn("MAIN_CHAT_SECRET_SHOULD_NOT_LEAK", user_content)
        self.assertIn("优化下计划", user_content)

    def test_it71_compat_dispatch_does_not_append_main_messages(self) -> None:
        """IT-71′: compat API must not pollute session.messages."""
        session = self._session()
        before = list(session.messages)
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({"reply": "收到", "operations": []}, ensure_ascii=False)
        )
        mock_llm._plan_model = "deepseek-v4-flash"
        self.agent._llm = mock_llm

        result = dispatch_plan_user_message(
            session,
            self.paths,
            {"type": "project.plan.message", "text": "加个任务做登录"},
        )
        self.assertEqual(session.messages, before)
        events = result.get("_events") or []
        types = [e.get("type") for e in events]
        self.assertIn("plan.subagent.start", types)
        self.assertIn("plan.subagent.done", types)
        self.assertNotIn("project.plan.bubble", types)
        self.assertNotIn("project.plan.auto_routed", types)

    def test_it191_query_tool_ok_plan_domain_write_blocked(self) -> None:
        sample = self.root / "bugs"
        sample.mkdir(exist_ok=True)
        note = sample / "note.txt"
        note.write_text("hello plan tools\n", encoding="utf-8")

        rel = str(note.relative_to(self.paths.agent_root)).replace("\\", "/")
        tr = execute_plan_tool(
            self.paths,
            self.pid,
            "read_file",
            {"path": rel},
        )
        self.assertTrue(tr.ok, tr.error.message if tr.error else tr)
        self.assertIn("hello plan tools", (tr.data or {}).get("content", ""))

        self.assertEqual(classify_plan_tool("read_file"), "query")
        self.assertEqual(classify_plan_tool("run_command"), "run")
        tasks_rel = str(self.tasks.relative_to(self.paths.agent_root)).replace("\\", "/")
        self.assertTrue(
            is_plan_domain_write_target(
                self.paths,
                self.pid,
                "write_text",
                {"path": tasks_rel},
            )
        )
        blocked = execute_plan_tool(
            self.paths,
            self.pid,
            "write_text",
            {
                "path": tasks_rel,
                "content": "- [ ] should not land\n",
            },
        )
        self.assertFalse(blocked.ok)
        self.assertIn("计划域", blocked.error.message if blocked.error else "")
        before = self.tasks.read_text(encoding="utf-8")
        self.assertNotIn("should not land", before)

    def test_plan_transcript_records_turns(self) -> None:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({"reply": "可以", "operations": []}, ensure_ascii=False)
        )
        mock_llm._plan_model = "deepseek-v4-flash"
        self.agent._llm = mock_llm
        out = self.agent.reason_about_intent("phase6 写在 map 合理吗")
        self.assertIn("可以", out)
        snap = self.agent.plan_transcript_snapshot()
        self.assertGreaterEqual(len(snap), 2)
        self.assertEqual(snap[0]["role"], "user")
        self.assertEqual(snap[-1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
