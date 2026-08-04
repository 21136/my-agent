"""Phase 39 — Plan behind-the-scenes subagent (S-200～S-202 · IT-200～IT-202)."""

from __future__ import annotations

import json
import secrets
import unittest
from unittest.mock import MagicMock

from llm_client import LLMResponse
from plan_agent import drop_plan_agent, get_plan_agent
from project_mode import (
    PLAN_DOMAIN_WRITE_BLOCK_MSG,
    create_project,
    main_agent_plan_domain_write_block,
    normalize_project_id,
    project_dir,
    project_root_rel,
)
from session import create_new, SessionMeta, utc_now_iso
from subagent import SubagentRunner
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths


class PlanSubagentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        self.pid = normalize_project_id(f"p39-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.map_path = self.root / "MAP.md"
        self.tasks = self.root / "TASKS.md"

    def tearDown(self) -> None:
        drop_plan_agent(self.pid)

    def _session(self):
        session = create_new(
            self.paths,
            conversation_id=f"_p39_{secrets.token_hex(3)}",
        )
        session.meta.project_id = self.pid
        session.meta.project_root = project_root_rel(self.pid)
        session.meta.active_shell = "project"
        session.meta.project_plan_status = "confirmed"
        session.messages = [
            {"role": "user", "content": "MAIN_CHAT_SECRET_SHOULD_NOT_LEAK"},
            {"role": "assistant", "content": "main agent reply"},
        ]
        session.save()
        return session

    def _executor(self, session) -> ToolExecutor:
        registry = ToolRegistry.load(self.paths)
        allow = {t.name for t in registry.session_evolved(["coding"])}
        allow.update({"write_text", "patch_file", "report_progress"})
        exec_session = ExecutorSession.load(
            session.session_dir,
            allowed_evolved=allow,
        )
        events: list[tuple[str, dict]] = []

        def on_event(et: str, payload: dict) -> None:
            events.append((et, payload))

        executor = ToolExecutor(
            registry=registry,
            session=exec_session,
            confirm_fn=lambda _p, _a: "y",
            on_event=on_event,
        )
        executor._test_events = events  # type: ignore[attr-defined]
        return executor

    def test_it200_run_plan_returns_summary_and_proposals(self) -> None:
        """IT-200: plan_partner path returns summary; no full tool transcript in main messages."""
        session = self._session()
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps(
                {
                    "reply": "已整理蔡岭模块规划",
                    "operations": [
                        {
                            "kind": "patch",
                            "path": "MAP.md",
                            "replacements": [
                                {
                                    "old": "# template MAP.md\n",
                                    "new": "# template MAP.md\n\n## 蔡岭模块\n",
                                }
                            ],
                            "reason": "add module pointer",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
        mock_llm._plan_model = "test"
        self.agent._llm = mock_llm

        runner = SubagentRunner(paths=self.paths)
        result = runner.run_plan("规划蔡岭模块", session=session, include_recent_user_lines=0)

        self.assertEqual(result.kind, "plan")
        self.assertIn("蔡岭", result.summary)
        self.assertTrue(result.adopt_pending or result.proposal_ids)
        # Main transcript unchanged by subagent run
        self.assertEqual(len(session.messages), 2)
        blob = json.dumps(session.messages, ensure_ascii=False)
        self.assertNotIn("tool_calls", blob)

    def test_it200_plan_partner_executor_emits_subagent_events(self) -> None:
        session = self._session()
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({"reply": "好", "operations": []}, ensure_ascii=False)
        )
        mock_llm._plan_model = "test"
        self.agent._llm = mock_llm

        executor = self._executor(session)
        result = executor.run(
            "plan_partner",
            {"task": "规划蔡岭模块", "include_recent_user_lines": 0},
        )
        self.assertTrue(result.ok, result.error)
        self.assertIn("summary", result.data or {})
        types = [et for et, _ in executor._test_events]  # type: ignore[attr-defined]
        self.assertIn("plan.subagent.start", types)
        self.assertIn("plan.subagent.done", types)

    def test_it201_no_auto_route_function(self) -> None:
        """IT-201: try_auto_route_user_to_plan removed; user.message always main turn."""
        import project_api

        self.assertFalse(hasattr(project_api, "try_auto_route_user_to_plan"))

    def test_it202_main_agent_map_write_rejected(self) -> None:
        """IT-202 / S-202: main Agent write_text MAP.md blocked with stable error code."""
        session = self._session()
        executor = self._executor(session)
        root = project_root_rel(self.pid)
        before = self.map_path.read_text(encoding="utf-8")

        result = executor.run(
            "run_evolved",
            {
                "tool_name": "write_text",
                "arguments": {"path": f"{root}/MAP.md", "content": "# hacked\n"},
            },
        )
        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertEqual(result.error.code, ToolErrorCode.PERMISSION_DENIED)
        self.assertTrue((result.error.details or {}).get("plan_domain_gate"))
        self.assertIn("plan_partner", result.error.message)
        self.assertEqual(self.map_path.read_text(encoding="utf-8"), before)

    def test_it202_block_reason_helper(self) -> None:
        root = project_root_rel(self.pid)
        reason = main_agent_plan_domain_write_block(
            project_root=root,
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {"path": f"{root}/MAP.md", "content": "x"},
            },
        )
        self.assertEqual(reason, PLAN_DOMAIN_WRITE_BLOCK_MSG)

    def test_s200_compat_dispatch_no_plan_bubbles(self) -> None:
        """S-200: compat project.plan.message emits subagent events, not plan bubbles."""
        from project_api import dispatch_plan_user_message

        session = self._session()
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({"reply": "收到", "operations": []}, ensure_ascii=False)
        )
        mock_llm._plan_model = "test"
        self.agent._llm = mock_llm

        result = dispatch_plan_user_message(
            session,
            self.paths,
            {"type": "project.plan.message", "text": "优化下计划"},
        )
        types = [e.get("type") for e in (result.get("_events") or [])]
        self.assertIn("plan.subagent.start", types)
        self.assertIn("plan.subagent.done", types)
        self.assertNotIn("project.plan.bubble", types)
        self.assertNotIn("project.plan.auto_routed", types)

    def test_plan_partner_requires_project(self) -> None:
        session = create_new(
            self.paths,
            conversation_id=f"_p39_nop_{secrets.token_hex(3)}",
        )
        session.meta = SessionMeta(
            topics=["coding"],
            updated_at=utc_now_iso(),
            phase="S4",
        )
        session.save()
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(session.session_dir, allowed_evolved=set()),
        )
        result = executor.run("plan_partner", {"task": "规划"})
        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertIn("project", result.error.message.lower())


class PlanSpawnTests(unittest.TestCase):
    def test_classify_plan_spawn_intent_parses_json(self) -> None:
        from plan_agent import classify_plan_spawn_intent

        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content='{"spawn": true, "reason": "规划域"}'
        )
        decision = classify_plan_spawn_intent("规划蔡岭模块", llm=mock_llm)
        self.assertTrue(decision.spawn)
        self.assertIn("规划", decision.reason)

    def test_classify_plan_spawn_continue_is_false(self) -> None:
        from plan_agent import classify_plan_spawn_intent

        mock_llm = MagicMock()
        decision = classify_plan_spawn_intent("继续", llm=mock_llm)
        self.assertFalse(decision.spawn)
        mock_llm.chat.assert_not_called()

    def test_run_turn_pre_spawn_emits_events(self) -> None:
        from agent import Agent
        from plan_agent import drop_plan_agent, get_plan_agent

        paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        pid = normalize_project_id(f"p39s-{secrets.token_hex(3)}")
        create_project(paths, pid)
        drop_plan_agent(pid)
        session = create_new(paths, conversation_id=f"_p39s_{secrets.token_hex(3)}")
        session.meta.project_id = pid
        session.meta.project_root = project_root_rel(pid)
        session.meta.active_shell = "project"
        session.meta.project_plan_status = "confirmed"
        session.meta.turn_mode = "agent"
        session.save()

        plan_agent = get_plan_agent(paths, pid)
        plan_mock = MagicMock()
        plan_mock.chat.return_value = MagicMock(
            content=json.dumps({"reply": "好", "operations": []}, ensure_ascii=False)
        )
        plan_mock._plan_model = "test"
        plan_agent._llm = plan_mock

        events: list[dict] = []

        class MockLLM:
            def __init__(self) -> None:
                self.responses = [
                    LLMResponse(
                        model="mock",
                        content='{"spawn": true, "reason": "规划"}',
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    ),
                    LLMResponse(
                        model="mock",
                        content="好的，已交给规划搭档处理。",
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    ),
                ]

            def chat(self, *_a, **_k) -> LLMResponse:
                return self.responses.pop(0)

            def set_cancel_event(self, _event):
                return None

        agent = Agent.create(session=session, llm=MockLLM())
        agent.on_turn_event = events.append

        result = agent.run_turn("规划蔡岭模块", spawn_explore=False, force_skip_plan_spawn=False)
        types = [e.get("type") for e in events]
        self.assertIn("plan.subagent.start", types)
        self.assertIn("plan.subagent.done", types)
        self.assertIn("[子代理摘要 · plan]", session.subagent_overlay or "")
        self.assertIsNotNone(result)
        drop_plan_agent(pid)


if __name__ == "__main__":
    unittest.main()
